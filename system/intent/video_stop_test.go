package intent

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestVideoStopOnlyWhenActive(t *testing.T) {
	active := true
	calls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/display/video":
			fmt.Fprintf(w, `{"active":%t}`, active)
		case "/display/video/stop":
			calls++
			w.WriteHeader(http.StatusOK)
		default:
			t.Errorf("unexpected route: %s", r.URL.Path)
		}
	}))
	defer server.Close()
	previous := videoHALBaseURL
	videoHALBaseURL = server.URL
	defer func() { videoHALBaseURL = previous }()

	for _, phrase := range []string{"stop", "enough", "off", "ok stop", "lampy stop", "hey lampy enough", "lampy off", "lampy, stop", "Speaker - PJ: stop", "[user] [ambient] Unknown Speaker: [voice:voice_46] Hey Lampy enough (audio saved at /tmp/clip)", "[voice-instruction] stop video\n[transcript] stop it", "Speaker - PJ: [voice-instruction] Lampy off\n[transcript] noisy words"} {
		if got := matchVideoStop(phrase); got == nil || got.Rule != "video_stop" {
			t.Errorf("active player: %q = %#v", phrase, got)
		}
	}
	before := calls
	for _, phrase := range []string{"turn off the light", "lampy turn off the light", "speaker - pj: lampy turn off the light", "[user] [ambient] unknown speaker: [voice:voice_46] stop tracking (audio saved at /tmp/clip)", "hey lampy stop tracking", "stop tracking", "there is enough light", "off to work"} {
		if got := matchVideoStop(phrase); got != nil {
			t.Errorf("unrelated command %q = %#v", phrase, got)
		}
	}
	if calls != before {
		t.Error("unrelated command stopped playback")
	}
	active = false
	if got := matchVideoStop("stop"); got != nil {
		t.Errorf("idle player = %#v", got)
	}
}

func TestVideoStopFailureIsNotReportedAsSuccess(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet {
			fmt.Fprint(w, `{"active":true}`)
			return
		}
		http.Error(w, "not stopped", http.StatusServiceUnavailable)
	}))
	defer server.Close()
	previous := videoHALBaseURL
	videoHALBaseURL = server.URL
	defer func() { videoHALBaseURL = previous }()
	got := matchVideoStop("enough")
	if got == nil || got.TTSText == "" || len(got.Actions) != 0 {
		t.Fatalf("failure response = %#v", got)
	}
}
