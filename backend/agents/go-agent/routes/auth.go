package routes

import (
	"net/http"
	"log"
	"fmt"
)

// AuthMiddleware 验证请求的 Bearer Token
func AuthMiddleware(cfg *Config, next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		authHeader := r.Header.Get("Authorization")
		if authHeader == "" {
			log.Printf("Authorization header missing")
			JsonResponse(w, http.StatusUnauthorized, map[string]string{"success": "false", "message": "Unauthorized"})
			return
		}

		token := ""
		n, err := fmt.Sscanf(authHeader, "Bearer %s", &token)
		if err != nil || n != 1 {
			log.Printf("Failed to parse authorization header: %v, parsed tokens: %d", err, n)
			JsonResponse(w, http.StatusUnauthorized, map[string]string{"success": "false", "message": "Invalid authorization header format"})
			return
		}

		if token != cfg.Token {
			log.Printf("Invalid token provided. Expected: %s, Got: %s", cfg.Token, token)
			JsonResponse(w, http.StatusUnauthorized, map[string]string{"success": "false", "message": "Invalid token"})
			return
		}
		next(w, r)
	}
}