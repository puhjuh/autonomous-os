package intent

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestDefaultVideoOnlyForTopicFreeRequests(t *testing.T) {
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

	for _, phrase := range []string{"show me a video", "play a youtube video", "Lampy, show me a video!", "Speaker - PJ: play a video", "[voice-instruction] play a youtube video\n[transcript] play something"} {
		if result := matchDefaultVideo(phrase); result == nil || result.Rule != "default_video" || result.TTSText != "" {
			t.Errorf("%q = %#v", phrase, result)
		}
	}
	before := calls
	for _, phrase := range []string{"show me a video about cats", "play a youtube video about woodworking", "play the video", "turn off the video", "show me video on my phone"} {
		if result := matchDefaultVideo(phrase); result != nil {
			t.Errorf("topic or unrelated request %q = %#v", phrase, result)
		}
	}
	if calls != before {
		t.Fatal("a topic or unrelated request invoked the default video")
	}
}
