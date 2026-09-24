package server

import (
	"github.com/gin-gonic/gin"
	"go.autonomous.ai/os/system/server/config"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestAdminAuthWithoutSessionRoutesToLoginOrSetup(t *testing.T) {
	gin.SetMode(gin.TestMode)
	for _, tc := range []struct {
		name   string
		cfg    config.Config
		bearer string
		want   int
	}{
		{name: "fresh device needs setup", want: http.StatusServiceUnavailable},
		{name: "subscription with password needs login", cfg: config.Config{AdminPasswordHash: "configured", SessionSecret: "test-session-secret"}, want: http.StatusUnauthorized},
		{name: "password without session secret still needs login", cfg: config.Config{AdminPasswordHash: "configured"}, want: http.StatusUnauthorized},
		{name: "invalid bearer cannot bypass password", cfg: config.Config{AdminPasswordHash: "configured", SessionSecret: "test-session-secret"}, bearer: "invalid", want: http.StatusUnauthorized},
		{name: "legacy API key needs login", cfg: config.Config{LLMAPIKey: "legacy-key"}, want: http.StatusUnauthorized},
		{name: "valid legacy key still works", cfg: config.Config{LLMAPIKey: "legacy-key"}, bearer: "legacy-key", want: http.StatusNoContent},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := gin.New()
			r.GET("/config", adminAuthMiddleware(&tc.cfg), func(c *gin.Context) { c.Status(http.StatusNoContent) })
			req := httptest.NewRequest(http.MethodGet, "/config", nil)
			if tc.bearer != "" {
				req.Header.Set("Authorization", "Bearer "+tc.bearer)
			}
			w := httptest.NewRecorder()
			r.ServeHTTP(w, req)
			if w.Code != tc.want {
				t.Fatalf("status=%d, want=%d: %s", w.Code, tc.want, w.Body.String())
			}
		})
	}
}
