package routes

import (
	"encoding/json"
	"net/http"
	"log"
)

// JsonResponse 是一个辅助函数，用于统一返回JSON响应
func JsonResponse(w http.ResponseWriter, statusCode int, data interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(statusCode)
	if data != nil {
		// 添加错误日志记录
		if statusCode >= 400 {
			log.Printf("Returning error response: %d, data: %v", statusCode, data)
		}
		json.NewEncoder(w).Encode(data)
	}
}