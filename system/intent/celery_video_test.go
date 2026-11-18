package intent

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestCeleryVideoRequestUsesSelectedURL(t *testing.T) {
	calls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.Method != http.MethodPost || r.URL.Path != "/display/video/play" {
			t.Errorf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
		var body struct {
			Query string `json:"query"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.Query != celeryManVideoURL {
			t.Errorf("query = %q, error = %v", body.Query, err)
		}
		w.WriteHeader(http.StatusAccepted)
	}))
	defer server.Close()
	previous := videoHALBaseURL
	videoHALBaseURL = server.URL
	defer func() { videoHALBaseURL = previous }()
	for _, phrase := range []string{"load up celery man", "Lampy, load up celery man!", "Lampy, load up celery, ma’am.", "load up celery ma'am", "Speaker - PJ: load up celery man", "[user] [ambient] Unknown Speaker: [voice:voice_46] Hey Lampy load up celery man (audio saved at /tmp/clip)", "[voice-instruction] load up celery man\n[transcript] celly men"} {
		if result := matchCeleryVideo(phrase); result == nil || result.Rule != "celery_video" || result.TTSText != "" {
			t.Errorf("%q = %#v", phrase, result)
		}
	}
	before := calls
	for _, phrase := range []string{"what is celery man", "load up celery man music", "load up celery", "turn off the light", "don't load up celery man", "please load up celery, ma'am"} {
		if result := matchCeleryVideo(phrase); result != nil {
			t.Errorf("unrelated %q = %#v", phrase, result)
		}
	}
	if calls != before {
		t.Fatal("unrelated request invoked playback")
	}
}

func TestCeleryVideoFailureIsNotReportedAsSuccess(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Error(w, "offline", http.StatusServiceUnavailable) }))
	defer server.Close()
	previous := videoHALBaseURL
	videoHALBaseURL = server.URL
	defer func() { videoHALBaseURL = previous }()
	result := matchCeleryVideo("load up celery man")
	if result == nil || result.TTSText == "" || len(result.Actions) != 0 {
		t.Fatalf("failure response = %#v", result)
	}
}
