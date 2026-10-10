package routes

import (
	"bufio"
	"fmt"
	"io"
	"net"
	"strings"
	"testing"
	"time"
)

func TestMihomoHTTPProxyProbeResponseCompatibility(t *testing.T) {
	for _, test := range []struct {
		name     string
		response string
		valid    bool
	}{
		// The production v1.19.32 core acknowledges CONNECT before validating
		// the target. This is a successful proxy handshake, even for CONNECT /.
		{"mihomo-connect", "HTTP/1.1 200 Connection established\r\n\r\n", true},
		{"http10-connect", "HTTP/1.0 200 Connection established\r\n\r\n", true},
		{"connect-reason-case", "HTTP/1.1 200 Connection Established\r\n\r\n", true},
		{"invalid-target", "HTTP/1.1 400 Bad Request\r\n\r\n", true},
		{"forbidden", "HTTP/1.1 403 Forbidden\r\n\r\n", true},
		{"method-rejected", "HTTP/1.1 405 Method Not Allowed\r\n\r\n", true},
		{"proxy-auth", "HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic\r\n\r\n", true},
		{"unrelated-http-server", "HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n", false},
		{"no-content", "HTTP/1.1 204 No Content\r\n\r\n", false},
		{"redirect", "HTTP/1.1 302 Found\r\nLocation: http://example.invalid/\r\n\r\n", false},
		{"origin-auth", "HTTP/1.1 401 Unauthorized\r\n\r\n", false},
		{"server-error", "HTTP/1.1 500 Internal Server Error\r\n\r\n", false},
		{"not-http", "SSH-2.0-unrelated-server\r\n", false},
		{"truncated-headers", "HTTP/1.1 200 Connection established\r\n", false},
		{"closed-without-response", "", false},
		{"oversized-headers", "HTTP/1.1 200 Connection established\r\nX-Padding: " + strings.Repeat("x", 16<<10) + "\r\n\r\n", false},
	} {
		t.Run(test.name, func(t *testing.T) {
			listener, err := net.Listen("tcp", "127.0.0.1:0")
			if err != nil {
				t.Fatal(err)
			}
			defer listener.Close()
			done := make(chan error, 1)
			go func() {
				conn, err := listener.Accept()
				if err != nil {
					done <- err
					return
				}
				defer conn.Close()
				conn.SetDeadline(time.Now().Add(2 * time.Second))
				reader := bufio.NewReader(io.LimitReader(conn, 4096))
				var request strings.Builder
				for {
					line, err := reader.ReadString('\n')
					if err != nil {
						done <- err
						return
					}
					request.WriteString(line)
					if line == "\r\n" {
						break
					}
				}
				if request.String() != "CONNECT / HTTP/1.1\r\nHost: /\r\nConnection: close\r\n\r\n" {
					done <- fmt.Errorf("probe used an unexpected or routable destination")
					return
				}
				_, err = io.WriteString(conn, test.response)
				done <- err
			}()
			err = probeMihomoProxy(listener.Addr().String(), "port")
			if serverErr := <-done; serverErr != nil {
				t.Fatal(serverErr)
			}
			if test.valid && err != nil {
				t.Fatalf("healthy proxy response rejected: %v", err)
			}
			if !test.valid && err == nil {
				t.Fatal("unrelated or malformed response passed proxy health")
			}
		})
	}
}
