package intent

import (
	"net/http"
	"strings"
	"time"
)

// This named request is a user-selected video, not a generic YouTube search.
const celeryManVideoURL = "https://www.youtube.com/watch?v=maAFcEU6atk"

var celeryVideoClient = &http.Client{Timeout: 10 * time.Second}

func isCeleryVideoRequest(text string) bool {
	text = strings.ToLower(strings.TrimSpace(text))
	const instruction = "[voice-instruction]"
	if pos := strings.Index(text, instruction); pos >= 0 && stripChitchatPrefixes(text[:pos]) == "" {
		text = strings.SplitN(text[pos+len(instruction):], "\n", 2)[0]
	} else {
		text = stripChitchatPrefixes(text)
	}
	text = strings.TrimSpace(strings.Trim(text, " \t\r\n.,!?:"))
	for _, prefix := range []string{"hello lampy", "hey lampy", "hi lampy", "okay lampy", "ok lampy", "lampy"} {
		if strings.HasPrefix(text, prefix) && len(text) > len(prefix) && strings.ContainsAny(text[len(prefix):len(prefix)+1], " ,:!") {
			text = strings.TrimSpace(strings.Trim(text[len(prefix):], " \t\r\n.,!?:"))
			break
		}
	}
	// Local STT heard “celery, ma’am” for this exact command on the Pi.
	// Normalize only punctuation here; wake authorization happens upstream.
	text = strings.NewReplacer(",", "", "’", "'").Replace(text)
	switch text {
	case "load up celery man", "load up the celery man", "please load up celery man", "load up celery man please", "load up celery ma'am", "load up celery maam":
		return true
	default:
		return false
	}
}

func matchCeleryVideo(text string) *Result {
	if !isCeleryVideoRequest(text) {
		return nil
	}
	body := `{"query":"` + celeryManVideoURL + `"}`
	resp, err := celeryVideoClient.Post(videoHALBaseURL+"/display/video/play", "application/json", strings.NewReader(body))
	if err != nil {
		return &Result{Rule: "celery_video", TTSText: "I couldn't start the video."}
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return &Result{Rule: "celery_video", TTSText: "I couldn't start the video."}
	}
	return &Result{Rule: "celery_video", Actions: []string{"POST /display/video/play"}}
}
