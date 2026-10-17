"""Set allocator defaults for the application process before Python starts."""
import os
import sys


DEFAULT_MALLOC_ARENA_MAX = '4'
DEFAULT_MALLOC_TRIM_THRESHOLD = '1048576'


def uses_glibc():
    """Other allocators and development hosts keep their existing behavior."""
    if sys.platform != 'linux':
        return False
    try:
        version = os.confstr('CS_GNU_LIBC_VERSION')
    except (AttributeError, OSError, ValueError):
        return False
    return bool(version and version.startswith('glibc '))


def application_environment(environ=None):
    """Fill only unset production defaults; never rewrite user tuning."""
    env = dict(os.environ if environ is None else environ)
    if (env.get('CONFIGFLOW_MEMORY_TUNING', '1') == '0'
            or env.get('LOG_LEVEL', 'INFO').upper() == 'DEBUG'
            or not uses_glibc()):
        return env

    tunables = {
        item.partition('=')[0]
        for item in env.get('GLIBC_TUNABLES', '').split(':')
    }
    if ('MALLOC_ARENA_MAX' not in env
            and 'glibc.malloc.arena_max' not in tunables):
        env['MALLOC_ARENA_MAX'] = DEFAULT_MALLOC_ARENA_MAX
    if ('MALLOC_TRIM_THRESHOLD_' not in env
            and 'glibc.malloc.trim_threshold' not in tunables):
        # A fixed trim threshold also disables glibc's dynamic mmap/trim
        # threshold adjustment. Do not replace either spelling of a user value.
        env['MALLOC_TRIM_THRESHOLD_'] = DEFAULT_MALLOC_TRIM_THRESHOLD
    return env


def main():
    # glibc reads these settings at process startup. Setting os.environ in app.py
    # would be too late. exec also keeps supervisor's PID and signal handling,
    # and imports the application only once, in the final interpreter.
    os.execve(
        sys.executable,
        [sys.executable, '-m', 'backend.app', *sys.argv[1:]],
        application_environment(),
    )


if __name__ == '__main__':
    main()
