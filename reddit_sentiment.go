package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
)

// ─── Reddit types ─────────────────────────────────────────────────────────────

type RedditListing struct {
	Data struct {
		Children []Child `json:"children"`
	} `json:"data"`
}

type Child struct {
	Kind string      `json:"kind"`
	Data CommentData `json:"data"`
}

type CommentData struct {
	ID       string   `json:"id"`
	Author   string   `json:"author"`
	Body     string   `json:"body"`
	Score    int      `json:"score"`
	Depth    int      `json:"depth"`
	Replies  *Replies `json:"replies"`
	Children []string `json:"children"`
	Count    int      `json:"count"`
}

type Replies struct {
	Data struct {
		Children []Child `json:"children"`
	} `json:"data"`
}

func (r *Replies) UnmarshalJSON(b []byte) error {
	s := string(b)
	if s == `""` || s == `null` {
		return nil
	}
	type Alias Replies
	var a Alias
	if err := json.Unmarshal(b, &a); err != nil {
		return err
	}
	*r = Replies(a)
	return nil
}

type Comment struct {
	Author string `json:"author"`
	Body   string `json:"body"`
	Score  int    `json:"score"`
	Depth  int    `json:"depth"`
}

// ─── HTTP helper ──────────────────────────────────────────────────────────────

var httpClient = &http.Client{Timeout: 30 * time.Second}

func fetchURL(url string) ([]byte, error) {
	req, _ := http.NewRequest("GET", url, nil)
	req.Header.Set("User-Agent", "GoRedditSentiment/2.0")
	for attempt := 0; attempt < 4; attempt++ {
		resp, err := httpClient.Do(req)
		if err != nil {
			return nil, err
		}
		body, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		if resp.StatusCode == 429 {
			wait := time.Duration(2<<attempt) * time.Second
			fmt.Fprintf(os.Stderr, "  rate-limited, waiting %s...\n", wait)
			time.Sleep(wait)
			continue
		}
		if resp.StatusCode != 200 {
			return nil, fmt.Errorf("HTTP %d", resp.StatusCode)
		}
		return body, nil
	}
	return nil, fmt.Errorf("too many retries")
}

func clamp(s string, n int) string {
	if len(s) > n {
		return s[:n]
	}
	return s
}

// ─── Reddit scraping ──────────────────────────────────────────────────────────

func expandMore(threadID string, ids []string) ([]Comment, error) {
	var all []Comment
	for i := 0; i < len(ids); i += 100 {
		end := i + 100
		if end > len(ids) {
			end = len(ids)
		}
		url := fmt.Sprintf(
			"https://api.reddit.com/api/morechildren?api_type=json&link_id=t3_%s&children=%s",
			threadID, strings.Join(ids[i:end], ","),
		)
		body, err := fetchURL(url)
		if err != nil {
			return nil, err
		}
		var resp struct {
			JSON struct {
				Data struct {
					Things []Child `json:"things"`
				} `json:"data"`
			} `json:"json"`
		}
		if err := json.Unmarshal(body, &resp); err != nil {
			return nil, err
		}
		for _, t := range resp.JSON.Data.Things {
			if t.Kind == "more" {
				continue
			}
			if t.Data.Body == "" || t.Data.Body == "[deleted]" || t.Data.Body == "[removed]" {
				continue
			}
			all = append(all, Comment{
				Author: t.Data.Author,
				Body:   t.Data.Body,
				Score:  t.Data.Score,
				Depth:  t.Data.Depth,
			})
		}
		time.Sleep(600 * time.Millisecond)
	}
	return all, nil
}

func buildComments(children []Child, threadID string) ([]Comment, error) {
	var out []Comment
	for _, child := range children {
		if child.Kind == "more" {
			fmt.Fprintf(os.Stderr, "  expanding %d more comments...\n", child.Data.Count)
			expanded, err := expandMore(threadID, child.Data.Children)
			if err != nil {
				fmt.Fprintf(os.Stderr, "  warning: %v\n", err)
				continue
			}
			out = append(out, expanded...)
			continue
		}
		if child.Data.Body == "" || child.Data.Body == "[deleted]" || child.Data.Body == "[removed]" {
			continue
		}
		out = append(out, Comment{
			Author: child.Data.Author,
			Body:   child.Data.Body,
			Score:  child.Data.Score,
			Depth:  child.Data.Depth,
		})
		if child.Data.Replies != nil {
			replies, err := buildComments(child.Data.Replies.Data.Children, threadID)
			if err != nil {
				return nil, err
			}
			out = append(out, replies...)
		}
	}
	return out, nil
}

func scrapeThread(threadURL string) ([]Comment, error) {
	parts := strings.Split(strings.TrimSuffix(threadURL, "/"), "/")
	var threadID string
	for i, p := range parts {
		if p == "comments" && i+1 < len(parts) {
			threadID = parts[i+1]
			break
		}
	}
	if threadID == "" {
		return nil, fmt.Errorf("could not extract thread ID from URL")
	}

	jsonURL := strings.TrimSuffix(threadURL, "/") + ".json?limit=500&sort=top"
	fmt.Fprintf(os.Stderr, "Fetching thread %s...\n", threadID)
	body, err := fetchURL(jsonURL)
	if err != nil {
		return nil, err
	}

	var listing []RedditListing
	if err := json.Unmarshal(body, &listing); err != nil {
		return nil, fmt.Errorf("parse error: %v", err)
	}
	if len(listing) < 2 {
		return nil, fmt.Errorf("unexpected response shape")
	}

	fmt.Fprintln(os.Stderr, "Building comment tree (expanding 'load more' batches)...")
	comments, err := buildComments(listing[1].Data.Children, threadID)
	if err != nil {
		return nil, err
	}
	fmt.Fprintf(os.Stderr, "Scraped %d comments\n", len(comments))
	return comments, nil
}

// ─── OpenAI analysis ──────────────────────────────────────────────────────────

type ClassifiedComment struct {
	Comment   Comment `json:"comment"`
	Topic     string  `json:"topic"`
	Sentiment string  `json:"sentiment"`
}

const systemPrompt = `You are analyzing Reddit comments about this news headline: "JD Vance says talks failed due to Iran's refusal to abandon nuclear program."

Classify each comment into EXACTLY one of these 5 topics:
1. Iran Nuclear Program — Iran's nuclear ambitions, enrichment, weapons capability
2. US-Iran Diplomacy — negotiations, diplomacy, sanctions, deal-making
3. Political Criticism — criticizing politicians or governments (US, Iran, or other)
4. Regional Conflict & War — war risk, Israel, Middle East conflict, military action
5. Skepticism & Cynicism — doubting the news/media, general cynicism, distrust, jokes

Also classify sentiment as: positive, negative, or neutral.

You will receive a JSON array. Reply ONLY with a JSON array in the same order:
[{"topic":"<exact topic name>","sentiment":"<positive|negative|neutral>"},...]

Raw JSON only. No explanation, no markdown fences.`

const defaultOpenAIModel = "gpt-5-mini"

func classifyBatch(apiKey, model string, comments []Comment) ([]ClassifiedComment, error) {
	type inputItem struct {
		I    int    `json:"i"`
		Body string `json:"body"`
	}
	var inputs []inputItem
	for i, c := range comments {
		inputs = append(inputs, inputItem{I: i, Body: clamp(c.Body, 300)})
	}
	inputJSON, _ := json.Marshal(inputs)

	reqBody, _ := json.Marshal(map[string]interface{}{
		"model":             model,
		"max_output_tokens": 1024,
		"input": []map[string]interface{}{
			{
				"role":    "system",
				"content": systemPrompt,
			},
			{
				"role":    "user",
				"content": "Comments:\n" + string(inputJSON),
			},
		},
		"text": map[string]interface{}{
			"format": map[string]interface{}{
				"type":        "json_schema",
				"name":        "comment_classifications",
				"description": "Topic and sentiment classification for each Reddit comment in order",
				"strict":      true,
				"schema": map[string]interface{}{
					"type": "object",
					"properties": map[string]interface{}{
						"results": map[string]interface{}{
							"type": "array",
							"items": map[string]interface{}{
								"type": "object",
								"properties": map[string]interface{}{
									"topic": map[string]interface{}{
										"type": "string",
										"enum": topics,
									},
									"sentiment": map[string]interface{}{
										"type": "string",
										"enum": []string{"positive", "negative", "neutral"},
									},
								},
								"required":             []string{"topic", "sentiment"},
								"additionalProperties": false,
							},
						},
					},
					"required":             []string{"results"},
					"additionalProperties": false,
				},
			},
		},
	})

	req, _ := http.NewRequest("POST", "https://api.openai.com/v1/responses", bytes.NewReader(reqBody))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+apiKey)

	resp, err := httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	respBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("OpenAI API %d: %s", resp.StatusCode, clamp(string(respBody), 300))
	}

	var cr struct {
		Output []struct {
			Type    string `json:"type"`
			Content []struct {
				Type string `json:"type"`
				Text string `json:"text"`
			} `json:"content"`
		} `json:"output"`
	}
	if err := json.Unmarshal(respBody, &cr); err != nil {
		return nil, fmt.Errorf("bad OpenAI response: %v", err)
	}

	var text string
	for _, item := range cr.Output {
		if item.Type != "message" {
			continue
		}
		for _, content := range item.Content {
			if content.Type == "output_text" {
				text = strings.TrimSpace(content.Text)
				break
			}
		}
		if text != "" {
			break
		}
	}
	if text == "" {
		return nil, fmt.Errorf("bad OpenAI response: no output text")
	}

	text = strings.TrimPrefix(text, "```json")
	text = strings.TrimPrefix(text, "```")
	text = strings.TrimSuffix(text, "```")
	text = strings.TrimSpace(text)

	var parsed struct {
		Results []struct {
			Topic     string `json:"topic"`
			Sentiment string `json:"sentiment"`
		} `json:"results"`
	}
	if err := json.Unmarshal([]byte(text), &parsed); err != nil {
		return nil, fmt.Errorf("parse: %v | raw: %s", err, clamp(text, 200))
	}

	var out []ClassifiedComment
	for i, c := range comments {
		topic := "Skepticism & Cynicism"
		sentiment := "neutral"
		if i < len(parsed.Results) {
			topic = parsed.Results[i].Topic
			sentiment = parsed.Results[i].Sentiment
		}
		out = append(out, ClassifiedComment{Comment: c, Topic: topic, Sentiment: sentiment})
	}
	return out, nil
}

func analyzeAll(apiKey, model string, comments []Comment) []ClassifiedComment {
	const batchSize = 50
	var all []ClassifiedComment
	total := len(comments)
	for i := 0; i < total; i += batchSize {
		end := i + batchSize
		if end > total {
			end = total
		}
		fmt.Fprintf(os.Stderr, "  classifying %d-%d / %d...\n", i+1, end, total)
		batch, err := classifyBatch(apiKey, model, comments[i:end])
		if err != nil {
			fmt.Fprintf(os.Stderr, "  batch error: %v — skipping\n", err)
			for _, c := range comments[i:end] {
				all = append(all, ClassifiedComment{Comment: c, Topic: "Skepticism & Cynicism", Sentiment: "neutral"})
			}
			time.Sleep(2 * time.Second)
			continue
		}
		all = append(all, batch...)
		time.Sleep(300 * time.Millisecond)
	}
	return all
}

// ─── Stats & HTML ─────────────────────────────────────────────────────────────

var topics = []string{
	"Iran Nuclear Program",
	"US-Iran Diplomacy",
	"Political Criticism",
	"Regional Conflict & War",
	"Skepticism & Cynicism",
}

var topicColors = []string{"#e8c84a", "#6fcf5a", "#f2635a", "#f0954a", "#7eb8f7"}

type TopicStats struct {
	Name        string
	Total       int
	Positive    int
	Negative    int
	Neutral     int
	Color       string
	TopComments []ClassifiedComment
}

func computeStats(classified []ClassifiedComment) []TopicStats {
	statsMap := map[string]*TopicStats{}
	for i, t := range topics {
		c := "#888"
		if i < len(topicColors) {
			c = topicColors[i]
		}
		statsMap[t] = &TopicStats{Name: t, Color: c}
	}

	for _, c := range classified {
		s, ok := statsMap[c.Topic]
		if !ok {
			s = statsMap["Skepticism & Cynicism"]
		}
		s.Total++
		switch c.Sentiment {
		case "positive":
			s.Positive++
		case "negative":
			s.Negative++
		default:
			s.Neutral++
		}
		if len(s.TopComments) < 3 && c.Comment.Score > 5 {
			s.TopComments = append(s.TopComments, c)
		}
	}

	var out []TopicStats
	for _, t := range topics {
		out = append(out, *statsMap[t])
	}
	return out
}

func esc(s string) string {
	s = strings.ReplaceAll(s, `\`, `\\`)
	s = strings.ReplaceAll(s, `"`, `\"`)
	s = strings.ReplaceAll(s, "\n", " ")
	s = strings.ReplaceAll(s, "\r", "")
	s = strings.ReplaceAll(s, "<", "&lt;")
	s = strings.ReplaceAll(s, ">", "&gt;")
	return s
}

func buildHTML(threadURL, model string, stats []TopicStats, total int) string {
	// Build JS data
	var topicNames, topicCounts, topicPos, topicNeg, topicNeu, colors []string
	for _, s := range stats {
		topicNames = append(topicNames, fmt.Sprintf("%q", s.Name))
		topicCounts = append(topicCounts, fmt.Sprintf("%d", s.Total))
		topicPos = append(topicPos, fmt.Sprintf("%d", s.Positive))
		topicNeg = append(topicNeg, fmt.Sprintf("%d", s.Negative))
		topicNeu = append(topicNeu, fmt.Sprintf("%d", s.Neutral))
		colors = append(colors, fmt.Sprintf("%q", s.Color))
	}

	// Build top comment cards HTML
	var cards strings.Builder
	for _, s := range stats {
		for _, c := range s.TopComments {
			body := clamp(c.Comment.Body, 200)
			sentBadgeColor := "#888"
			if c.Sentiment == "positive" {
				sentBadgeColor = "#6fcf5a"
			} else if c.Sentiment == "negative" {
				sentBadgeColor = "#f2635a"
			}
			cards.WriteString(fmt.Sprintf(`
<div class="comment-card" data-topic=%q>
  <div class="comment-topic" style="color:%s">%s</div>
  <div class="comment-body">%s</div>
  <div class="comment-meta">
    <span>u/%s</span>
    <span>↑%d</span>
    <span class="sent-dot" style="background:%s">%s</span>
  </div>
</div>`, s.Name, s.Color, esc(s.Name), esc(body), esc(c.Comment.Author), c.Comment.Score, sentBadgeColor, c.Sentiment))
		}
	}

	return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reddit Sentiment Analysis</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Syne:wght@400;700;800&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0c0c0c;--surface:#151515;--surface2:#1c1c1c;--border:#252525;
  --text:#ede9df;--muted:#666;--accent:#e8c84a;
}
body{background:var(--bg);color:var(--text);font-family:'Syne',sans-serif;min-height:100vh}
a{color:inherit;text-decoration:none}

header{padding:2.5rem 3rem 2rem;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:1rem}
header h1{font-size:clamp(1.6rem,4vw,2.8rem);font-weight:800;letter-spacing:-0.04em;line-height:1}
header h1 em{color:var(--accent);font-style:normal}
header .meta{font-family:'DM Mono',monospace;font-size:11px;color:var(--muted);text-align:right;line-height:1.8}
header .meta a{color:var(--muted);text-decoration:underline;text-underline-offset:3px}

.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--border);border-bottom:1px solid var(--border)}
.metric{background:var(--surface);padding:1.5rem 2rem}
.metric-n{font-size:clamp(2rem,5vw,3.5rem);font-weight:800;letter-spacing:-0.05em;line-height:1}
.metric-l{font-family:'DM Mono',monospace;font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;margin-top:6px}

.main{max-width:1300px;margin:0 auto;padding:3rem 2rem}

.chart-section{display:grid;grid-template-columns:1fr 1fr;gap:3rem;align-items:center;margin-bottom:4rem}
@media(max-width:800px){.chart-section{grid-template-columns:1fr}}
.chart-wrap{position:relative;width:100%;max-width:420px;margin:0 auto}
.chart-center{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);text-align:center;pointer-events:none}
.chart-center-n{font-size:2.4rem;font-weight:800;letter-spacing:-0.05em}
.chart-center-l{font-family:'DM Mono',monospace;font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;margin-top:4px}

.legend{display:flex;flex-direction:column;gap:1rem}
.legend-item{display:flex;align-items:flex-start;gap:12px;cursor:pointer;padding:10px 14px;border-radius:6px;border:1px solid transparent;transition:border-color .15s,background .15s}
.legend-item:hover{background:var(--surface);border-color:var(--border)}
.legend-item.active{background:var(--surface2);border-color:var(--border)}
.legend-dot{width:12px;height:12px;border-radius:2px;flex-shrink:0;margin-top:3px}
.legend-name{font-size:14px;font-weight:700;margin-bottom:4px}
.legend-bar-wrap{display:flex;gap:3px;height:4px;border-radius:2px;overflow:hidden;width:100%;margin:6px 0}
.legend-meta{font-family:'DM Mono',monospace;font-size:10px;color:var(--muted)}

.section-title{font-family:'DM Mono',monospace;font-size:10px;text-transform:uppercase;letter-spacing:.12em;color:var(--muted);margin-bottom:1.5rem}

.bar-chart-section{margin-bottom:4rem}
.bar-chart-wrap{position:relative;height:280px}

.comments-section{}
.comments-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:1rem;margin-top:0}
.comment-card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:1.2rem;display:flex;flex-direction:column;gap:10px;transition:border-color .15s}
.comment-card:hover{border-color:#333}
.comment-topic{font-family:'DM Mono',monospace;font-size:10px;font-weight:500;text-transform:uppercase;letter-spacing:.08em}
.comment-body{font-size:13px;line-height:1.6;color:#bbb;flex:1}
.comment-meta{display:flex;align-items:center;gap:10px;font-family:'DM Mono',monospace;font-size:11px;color:var(--muted)}
.sent-dot{font-size:10px;padding:2px 8px;border-radius:3px;color:#000;font-weight:500}

footer{border-top:1px solid var(--border);padding:1.5rem 2rem;font-family:'DM Mono',monospace;font-size:11px;color:var(--muted);text-align:center;margin-top:4rem}
</style>
</head>
<body>

<header>
  <div>
    <h1>Reddit <em>Sentiment</em></h1>
    <div style="font-family:'DM Mono',monospace;font-size:12px;color:var(--muted);margin-top:8px">JD Vance / Iran Nuclear Talks · r/worldnews</div>
  </div>
  <div class="meta">
    <div>` + fmt.Sprintf("%d", total) + ` comments analyzed with OpenAI (` + esc(model) + `)</div>
    <div>Generated ` + time.Now().Format("Jan 2, 2006 15:04") + `</div>
    <div><a href="` + threadURL + `" target="_blank">view thread ↗</a></div>
  </div>
</header>

<div class="metrics">
  <div class="metric">
    <div class="metric-n">` + fmt.Sprintf("%d", total) + `</div>
    <div class="metric-l">total comments</div>
  </div>
  <div class="metric">
    <div class="metric-n" style="color:#6fcf5a" id="m-pos">—</div>
    <div class="metric-l">positive</div>
  </div>
  <div class="metric">
    <div class="metric-n" style="color:#f2635a" id="m-neg">—</div>
    <div class="metric-l">negative</div>
  </div>
</div>

<div class="main">

  <div class="chart-section">
    <div>
      <div class="chart-wrap">
        <canvas id="donut" role="img" aria-label="Donut chart showing comment distribution by topic"></canvas>
        <div class="chart-center">
          <div class="chart-center-n" style="color:var(--accent)">` + fmt.Sprintf("%d", total) + `</div>
          <div class="chart-center-l">comments</div>
        </div>
      </div>
    </div>
    <div class="legend" id="legend"></div>
  </div>

  <div class="bar-chart-section">
    <div class="section-title">Sentiment breakdown per topic</div>
    <div class="bar-chart-wrap">
      <canvas id="bar-chart" role="img" aria-label="Stacked bar chart of sentiment by topic"></canvas>
    </div>
  </div>

  <div class="comments-section">
    <div class="section-title">Top comments by topic</div>
    <div class="comments-grid">` + cards.String() + `</div>
  </div>

</div>

<footer>built with go + openai · reddit r/worldnews · ` + time.Now().Format("2006") + `</footer>

<script>
const TOPICS  = [` + strings.Join(topicNames, ",") + `];
const COUNTS  = [` + strings.Join(topicCounts, ",") + `];
const POS     = [` + strings.Join(topicPos, ",") + `];
const NEG     = [` + strings.Join(topicNeg, ",") + `];
const NEU     = [` + strings.Join(topicNeu, ",") + `];
const COLORS  = [` + strings.Join(colors, ",") + `];

const totalPos = POS.reduce((a,b)=>a+b,0);
const totalNeg = NEG.reduce((a,b)=>a+b,0);
document.getElementById('m-pos').textContent = totalPos;
document.getElementById('m-neg').textContent = totalNeg;

// Donut chart
const donut = new Chart(document.getElementById('donut'), {
  type: 'doughnut',
  data: {
    labels: TOPICS,
    datasets: [{
      data: COUNTS,
      backgroundColor: COLORS,
      borderWidth: 2,
      borderColor: '#0c0c0c',
      hoverOffset: 10
    }]
  },
  options: {
    responsive: true,
    cutout: '68%',
    plugins: { legend: { display: false }, tooltip: {
      callbacks: {
        label: ctx => {
          const pct = Math.round(ctx.parsed / COUNTS.reduce((a,b)=>a+b,0) * 100);
          return ' ' + ctx.parsed + ' comments (' + pct + '%)';
        }
      },
      bodyFont: { family: "'DM Mono'" },
      titleFont: { family: "'Syne'", weight: '700' },
      backgroundColor: '#1c1c1c',
      borderColor: '#333',
      borderWidth: 1
    }}
  }
});

// Legend
const legend = document.getElementById('legend');
TOPICS.forEach((t, i) => {
  const total = COUNTS[i];
  const posW = Math.round(POS[i]/total*100)||0;
  const negW = Math.round(NEG[i]/total*100)||0;
  const neuW = 100 - posW - negW;
  const el = document.createElement('div');
  el.className = 'legend-item';
  el.innerHTML = '<div class="legend-dot" style="background:'+COLORS[i]+'"></div>' +
    '<div style="flex:1">' +
      '<div class="legend-name">'+t+'</div>' +
      '<div class="legend-bar-wrap">' +
        '<div style="width:'+posW+'%;background:#6fcf5a"></div>' +
        '<div style="width:'+negW+'%;background:#f2635a"></div>' +
        '<div style="width:'+neuW+'%;background:#333"></div>' +
      '</div>' +
      '<div class="legend-meta">'+total+' comments &nbsp;·&nbsp; '+posW+'% pos &nbsp;'+negW+'% neg</div>' +
    '</div>';
  legend.appendChild(el);
});

// Stacked bar
new Chart(document.getElementById('bar-chart'), {
  type: 'bar',
  data: {
    labels: TOPICS.map(t => t.length > 22 ? t.slice(0,22)+'…' : t),
    datasets: [
      { label: 'Positive', data: POS, backgroundColor: '#6fcf5a' },
      { label: 'Negative', data: NEG, backgroundColor: '#f2635a' },
      { label: 'Neutral',  data: NEU, backgroundColor: '#2a2a2a' }
    ]
  },
  options: {
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { labels: { color:'#666', font:{ family:"'DM Mono'", size:11 }, boxWidth:10, padding:16 } } },
    scales: {
      x: { stacked:true, ticks:{ color:'#555', font:{ family:"'DM Mono'", size:10 } }, grid:{ color:'#1a1a1a' } },
      y: { stacked:true, ticks:{ color:'#555', font:{ family:"'DM Mono'", size:10 } }, grid:{ color:'#1a1a1a' } }
    }
  }
});
</script>
</body>
</html>`
}

// ─── Main ─────────────────────────────────────────────────────────────────────

func main() {
	threadURL := "https://www.reddit.com/r/worldnews/comments/1sjch2q/jd_vance_says_talks_failed_due_to_irans_refusal/"
	cacheFile := "comments_cache.json"
	outFile := "report.html"

	// API key from env
	apiKey := os.Getenv("OPENAI_API_KEY")
	if apiKey == "" {
		fmt.Fprintln(os.Stderr, "Error: set OPENAI_API_KEY environment variable")
		os.Exit(1)
	}
	model := os.Getenv("OPENAI_MODEL")
	if model == "" {
		model = defaultOpenAIModel
	}

	// ── Step 1: load or scrape comments ──
	var comments []Comment

	if data, err := os.ReadFile(cacheFile); err == nil {
		fmt.Fprintf(os.Stderr, "Found cache file %s — skipping scrape\n", cacheFile)
		if err := json.Unmarshal(data, &comments); err != nil {
			fmt.Fprintf(os.Stderr, "Cache parse error: %v — re-scraping\n", err)
			comments = nil
		}
	}

	if comments == nil {
		var err error
		comments, err = scrapeThread(threadURL)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Scrape error: %v\n", err)
			os.Exit(1)
		}
		data, _ := json.MarshalIndent(comments, "", "  ")
		if err := os.WriteFile(cacheFile, data, 0644); err != nil {
			fmt.Fprintf(os.Stderr, "Warning: could not save cache: %v\n", err)
		} else {
			fmt.Fprintf(os.Stderr, "Saved %d comments to %s\n", len(comments), cacheFile)
		}
	}

	// ── Step 2: OpenAI classification ──
	fmt.Fprintf(os.Stderr, "Classifying %d comments with OpenAI model %s...\n", len(comments), model)
	classified := analyzeAll(apiKey, model, comments)

	// ── Step 3: build report ──
	stats := computeStats(classified)
	html := buildHTML(threadURL, model, stats, len(classified))
	if err := os.WriteFile(outFile, []byte(html), 0644); err != nil {
		fmt.Fprintf(os.Stderr, "Write error: %v\n", err)
		os.Exit(1)
	}
	fmt.Fprintf(os.Stderr, "\nDone! Open %s in your browser.\n", outFile)
}
