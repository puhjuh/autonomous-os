package intent

import (
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"time"

	"go.autonomous.ai/os/system/lib/hal"
)

// The status request runs only for the exact short commands below. Both calls
// are bounded so a stopped or unavailable optional player cannot stall speech.
var videoStatusClient = &http.Client{Timeout: 400 * time.Millisecond}
var videoStopClient = &http.Client{Timeout: 10 * time.Second}
var videoHALBaseURL = hal.BaseURL

func isVideoStopPhrase(text string) bool {
	text = strings.ToLower(strings.TrimSpace(text))
	// Realtime delegation may wrap the clean instruction in the same speaker
	// envelope as a direct voice turn. Do not match its noisy transcript.
	const instruction = "[voice-instruction]"
	if pos := strings.Index(text, instruction); pos >= 0 && stripChitchatPrefixes(text[:pos]) == "" {
		text = strings.SplitN(text[pos+len(instruction):], "\n", 2)[0]
	} else {
		text = stripChitchatPrefixes(text)
	}
	text = strings.TrimSpace(strings.Trim(text, " \t\r\n.,!?"))
	for _, prefix := range []string{"hey lampy", "okay lampy", "ok lampy", "lampy"} {
		if strings.HasPrefix(text, prefix) && len(text) > len(prefix) && strings.ContainsAny(text[len(prefix):len(prefix)+1], " ,:!") {
			text = strings.TrimSpace(strings.Trim(text[len(prefix):], " \t\r\n.,!?:"))
			break
		}
	}
	switch text {
	case "stop", "enough", "off", "please stop", "stop please", "that's enough", "that is enough", "ok stop", "okay stop", "stop playing", "stop playback", "stop the video", "stop video", "video off", "turn off the video":
		return true
	default:
		return false
	}
}

func matchVideoStop(text string) *Result {
	if !isVideoStopPhrase(text) {
		return nil
	}
	resp, err := videoStatusClient.Get(videoHALBaseURL + "/display/video")
	if err != nil {
		return nil
	}
	defer resp.Body.Close()
	var status struct {
		Active bool `json:"active"`
	}
	if resp.StatusCode != http.StatusOK || json.NewDecoder(io.LimitReader(resp.Body, 8192)).Decode(&status) != nil || !status.Active {
		return nil
	}
	stop, err := videoStopClient.Post(videoHALBaseURL+"/display/video/stop", "application/json", strings.NewReader("{}"))
	if err != nil {
		return &Result{Rule: "video_stop", TTSText: "I couldn't stop the video."}
	}
	defer stop.Body.Close()
	if stop.StatusCode >= 300 {
		return &Result{Rule: "video_stop", TTSText: "I couldn't stop the video."}
	}
	return &Result{Rule: "video_stop", Actions: []string{"POST /display/video/stop"}}
}
