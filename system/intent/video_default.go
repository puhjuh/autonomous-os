package intent

import "strings"

// Only exact, topic-free requests select PJ's current default. Requests with a
// subject remain with the agent so they can search for that subject.
func isDefaultVideoRequest(text string) bool {
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
	switch text {
	case "show me a video", "show me a youtube video", "show a video", "show a youtube video", "play a video", "play a youtube video", "play youtube video", "play me a video", "play me a youtube video", "please show me a video", "please show me a youtube video", "please play a video", "please play a youtube video":
		return true
	default:
		return false
	}
}

func matchDefaultVideo(text string) *Result {
	if !isDefaultVideoRequest(text) {
		return nil
	}
	result := matchCeleryVideo("load up celery man")
	result.Rule = "default_video"
	return result
}
