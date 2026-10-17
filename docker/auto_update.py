#!/usr/bin/env python3
"""Run the latest source on container start (opt-in: CONFIGFLOW_AUTO_UPDATE=1).

Fetches the newest commit of CONFIGFLOW_UPDATE_BRANCH (default main) from
CONFIGFLOW_UPDATE_REPO (default thsrite/configflow), rebuilds only what changed
(frontend with npm, Agent binaries with Go) and installs it over the image's
copy. Builds and caches live under /data/.auto-update, so recreating the
container does not rebuild. Any failure keeps the last good build, or the
image's own code when there is none: startup must never be blocked.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request

REPO = os.environ.get('CONFIGFLOW_UPDATE_REPO', 'thsrite/configflow')
BRANCH = os.environ.get('CONFIGFLOW_UPDATE_BRANCH', 'main')
STATE = os.path.join(os.environ.get('DATA_DIR', '/data'), '.auto-update')
BACKEND, HTML, AGENTS = '/app/backend', '/usr/share/nginx/html', '/opt/configflow/static/agents'
IMAGE_COPY = '/app/.image'  # the image's own code, restored when no build is usable
ARCHES = (('amd64', {}), ('arm64', {}), ('armv7', {'GOARCH': 'arm', 'GOARM': '7'}))


def log(message):
    print(f'[auto-update] {message}', flush=True)


def fetch(url, timeout=30):
    request = urllib.request.Request(url, headers={'User-Agent': 'configflow-auto-update',
                                                   'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def tree_hash(path):
    """Content hash of a source tree, to rebuild only what changed."""
    digest = hashlib.sha256()
    for root, dirs, files in os.walk(path):
        dirs[:] = sorted(d for d in dirs if d not in ('node_modules', 'dist', '.git'))
        for name in sorted(files):
            full = os.path.join(root, name)
            digest.update(os.path.relpath(full, path).encode())
            with open(full, 'rb') as handle:
                digest.update(hashlib.sha256(handle.read()).digest())
    return digest.hexdigest()


def run(args, cwd, env=None, timeout=900):
    merged = dict(os.environ, **(env or {}))
    result = subprocess.run(args, cwd=cwd, env=merged, timeout=timeout,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if result.returncode != 0:
        raise RuntimeError(f'{" ".join(args)} failed:\n{result.stdout[-2000:]}')


def load_manifest():
    try:
        with open(os.path.join(STATE, 'manifest.json')) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def save_manifest(manifest):
    path = os.path.join(STATE, 'manifest.json')
    with open(path + '.tmp', 'w') as handle:
        json.dump(manifest, handle, indent=2)
    os.replace(path + '.tmp', path)


def download(sha):
    source = os.path.join(STATE, f'src-{sha}')
    if os.path.isdir(source):
        return source
    archive = os.path.join(STATE, f'{sha}.tar.gz')
    with open(archive, 'wb') as handle:
        handle.write(fetch(f'https://codeload.github.com/{REPO}/tar.gz/{sha}', timeout=120))
    staging = source + '.tmp'
    shutil.rmtree(staging, ignore_errors=True)
    with tarfile.open(archive) as bundle:
        try:
            bundle.extractall(staging, filter='data')
        except TypeError:  # Python without extraction filters
            bundle.extractall(staging)
    os.remove(archive)
    (top,) = os.listdir(staging)  # codeload wraps everything in <repo>-<sha>/
    os.replace(os.path.join(staging, top), source)
    shutil.rmtree(staging, ignore_errors=True)
    return source


def build_frontend(source, manifest):
    frontend = os.path.join(source, 'frontend')
    key = tree_hash(frontend)
    out = os.path.join(STATE, f'frontend-{key[:16]}')
    lock_key = hashlib.sha256(open(os.path.join(frontend, 'package-lock.json'), 'rb').read()).hexdigest()[:16]
    # Node resolves packages by walking up to directories literally named
    # node_modules (on the symlink's real path), so keep that name.
    modules = os.path.join(STATE, f'deps-{lock_key}', 'node_modules')
    manifest['deps'] = os.path.dirname(modules)  # kept by prune even when the build is reused
    if not os.path.isdir(out):
        if not os.path.isdir(modules):
            log('installing frontend dependencies (package-lock changed)')
            run(['npm', 'ci', '--no-audit', '--no-fund', '--cache', os.path.join(STATE, 'npm-cache')], frontend)
            os.makedirs(os.path.dirname(modules), exist_ok=True)
            shutil.move(os.path.join(frontend, 'node_modules'), modules)
        os.symlink(modules, os.path.join(frontend, 'node_modules'))
        log('building frontend')
        run(['npm', 'run', 'build'], frontend)
        shutil.copytree(os.path.join(frontend, 'dist'), out + '.tmp')
        os.replace(out + '.tmp', out)
    manifest['frontend'] = out
    return out


def build_agents(source, manifest):
    agent = os.path.join(source, 'backend', 'agents', 'go-agent')
    key = tree_hash(agent)
    out = os.path.join(STATE, f'agents-{key[:16]}')
    if not os.path.isdir(out):
        log('building Agent binaries')
        staging = out + '.tmp'
        shutil.rmtree(staging, ignore_errors=True)
        os.makedirs(staging)
        env = {'CGO_ENABLED': '0', 'GOOS': 'linux', 'GOPROXY': os.environ.get('GOPROXY', 'https://goproxy.cn,direct'),
               'GOCACHE': os.path.join(STATE, 'go-cache'), 'GOMODCACHE': os.path.join(STATE, 'go-mod'),
               'PATH': '/usr/local/go/bin:' + os.environ.get('PATH', '')}
        for name, extra in ARCHES:
            arch_env = dict(env, GOARCH=name)
            arch_env.update(extra)  # armv7 is GOARCH=arm with GOARM=7
            run(['go', 'build', '-ldflags=-s -w', '-o', os.path.join(staging, f'configflow-agent-linux-{name}'), '.'],
                agent, env=arch_env)
        os.replace(staging, out)
    manifest['agents'] = out
    return out


def install_requirements(source, manifest):
    requirements = os.path.join(source, 'backend', 'requirements.txt')
    key = hashlib.sha256(open(requirements, 'rb').read()).hexdigest()
    image_key = manifest.setdefault('image_requirements', _image_requirements_hash())
    if key not in (image_key, manifest.get('requirements')):
        log('installing backend requirements')
        run([sys.executable, '-m', 'pip', 'install', '-q', '-r', requirements], source)
    manifest['requirements'] = key


def _image_requirements_hash():
    marker = '/app/.image-requirements.sha256'
    try:
        return open(marker).read().strip()
    except OSError:
        return ''


def replace_dir(src, dst):
    # Directories from image layers cannot be renamed on overlayfs (EXDEV), so
    # delete and copy. Services are not running yet; a failure here is undone
    # by restoring the preserved image copy.
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=True)


def preserve_image():
    """Keep the image's code once per container, so a failed update can return to it."""
    if os.path.isdir(IMAGE_COPY):
        return
    staging = IMAGE_COPY + '.tmp'
    shutil.rmtree(staging, ignore_errors=True)
    for name, path in (('backend', BACKEND), ('html', HTML), ('agents', AGENTS)):
        if os.path.isdir(path):
            shutil.copytree(path, os.path.join(staging, name), symlinks=True)
    os.replace(staging, IMAGE_COPY)


def restore_image():
    for name, path in (('backend', BACKEND), ('html', HTML), ('agents', AGENTS)):
        saved = os.path.join(IMAGE_COPY, name)
        if os.path.isdir(saved):
            replace_dir(saved, path)


def install(manifest):
    """Copy the recorded build over the image (also after a container recreate)."""
    source = manifest.get('source')
    if not source or not os.path.isdir(source):
        restore_image()
        return False
    replace_dir(os.path.join(source, 'backend'), BACKEND)
    version = os.path.join(BACKEND, 'version.py')
    with open(version) as handle:
        text = handle.read()
    with open(version, 'w') as handle:
        handle.write(text.replace('__version__ = "dev"', f'__version__ = "{BRANCH}-{manifest["sha"][:7]}"'))
    if manifest.get('frontend') and os.path.isdir(manifest['frontend']):
        replace_dir(manifest['frontend'], HTML)
    if manifest.get('agents') and os.path.isdir(manifest['agents']):
        replace_dir(manifest['agents'], AGENTS)
    return True


def prune(manifest):
    """Drop sources and builds the manifest no longer points at (caches stay)."""
    keep = {os.path.basename(manifest.get(k) or '') for k in ('source', 'frontend', 'agents', 'deps')}
    for name in os.listdir(STATE):
        path = os.path.join(STATE, name)
        built = name.startswith(('src-', 'frontend-', 'agents-', 'deps-'))
        leftover = name.endswith(('.tmp', '.tar.gz'))
        if (built and name not in keep) or leftover:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)


def main():
    if os.environ.get('CONFIGFLOW_AUTO_UPDATE') != '1':
        return
    os.makedirs(STATE, exist_ok=True)
    preserve_image()
    manifest = load_manifest()
    started = time.time()
    try:
        sha = json.loads(fetch(f'https://api.github.com/repos/{REPO}/commits/{BRANCH}'))['sha']
    except Exception as error:  # noqa: BLE001 - offline must not block startup
        log(f'cannot reach GitHub ({error.__class__.__name__}); using {manifest.get("sha", "image")[:7]}')
        install(manifest)
        return
    if sha == manifest.get('sha'):
        log(f'{BRANCH} is at {sha[:7]}, already built')
        install(manifest)
        return
    log(f'updating to {REPO}@{BRANCH} {sha[:7]}')
    candidate = dict(manifest)
    try:
        candidate['source'] = download(sha)
        if shutil.which('npm'):
            build_frontend(candidate['source'], candidate)
        else:
            log('npm not available, keeping the bundled frontend')
        if os.path.exists('/usr/local/go/bin/go'):
            build_agents(candidate['source'], candidate)
        else:
            log('Go not available, keeping the bundled Agent binaries')
        install_requirements(candidate['source'], candidate)
        candidate['sha'] = sha
        install(candidate)
        save_manifest(candidate)
        prune(candidate)
        log(f'running {sha[:7]} (took {time.time() - started:.0f}s)')
    except Exception as error:  # noqa: BLE001 - fall back, never block startup
        log(f'update failed, keeping {manifest.get("sha", "image")[:7]}: {error}')
        try:
            install(manifest)
        except Exception as restore_error:  # noqa: BLE001
            log(f'restoring the previous build failed ({restore_error}); using the image code')
            restore_image()


if __name__ == '__main__':
    main()
