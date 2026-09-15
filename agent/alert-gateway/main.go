package main

import (
	"bytes"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"database/sql"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	_ "modernc.org/sqlite"
)

const maxEventBytes = 64 << 10

type config struct {
	addr, dataDir, hermesURL, hermesSecret, host string
	projectsFile                                 string
	retentionDays                                int
	cooldownSeconds                              int
	warningCooldownSeconds                       int
}

type incomingEvent struct {
	Source  string `json:"source"`
	Message string `json:"message"`
}

type event struct {
	ID, Source, Severity, Message, Fingerprint string
	CreatedAt                                  int64
}

type app struct {
	db  *sql.DB
	cfg config
}

type projectSettings struct {
	Projects map[string]struct {
		Programs map[string]bool `json:"programs"`
	} `json:"projects"`
}

type logContext struct {
	Project string
	Program string
	LogFile string
	DLL     string
	Enabled bool
}

var (
	volatileField = regexp.MustCompile(`(?i)(\b(?:tag(?:[_-]?(?:slug|id))?|page|attempt|delayms|requestid|traceid|cursor|offset|limit|timestamp)=)[^,;\s\])]+`)
	uuidLike      = regexp.MustCompile(`(?i)\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b`)
	timestampLike = regexp.MustCompile(`\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?`)
	longNumber    = regexp.MustCompile(`\b\d{4,}\b`)
	dllLike       = regexp.MustCompile(`(?i)\b[A-Za-z0-9_.-]+\.dll\b`)
)

func main() {
	if len(os.Args) == 2 && os.Args[1] == "healthcheck" {
		response, err := http.Get("http://127.0.0.1:9080/health") // #nosec G107 -- fixed loopback health endpoint
		if err != nil || response.StatusCode != http.StatusOK {
			os.Exit(1)
		}
		_ = response.Body.Close()
		return
	}
	cfg := config{
		addr:                   envOr("LISTEN_ADDR", "0.0.0.0:9080"),
		dataDir:                envOr("DATA_DIR", "/var/lib/alert-gateway"),
		hermesURL:              os.Getenv("HERMES_WEBHOOK_URL"),
		hermesSecret:           os.Getenv("HERMES_WEBHOOK_SECRET"),
		host:                   envOr("ALERT_HOST", "unknown-host"),
		projectsFile:           envOr("PROJECTS_FILE", "/etc/alert-gateway/projects.json"),
		retentionDays:          envPositiveInt("RETENTION_DAYS", 7),
		cooldownSeconds:        envPositiveInt("ALERT_COOLDOWN_SECONDS", 900),
		warningCooldownSeconds: envPositiveInt("WARNING_COOLDOWN_SECONDS", 1800),
	}
	if cfg.hermesURL == "" || cfg.hermesSecret == "" {
		log.Fatal("HERMES_WEBHOOK_URL and HERMES_WEBHOOK_SECRET are required")
	}
	if _, err := loadProjectSettings(cfg.projectsFile); err != nil {
		log.Fatalf("invalid project configuration: %v", err)
	}
	if err := os.MkdirAll(cfg.dataDir, 0o700); err != nil {
		log.Fatal(err)
	}
	db, err := sql.Open("sqlite", filepath.Join(cfg.dataDir, "events.db"))
	if err != nil {
		log.Fatal(err)
	}
	// SQLite has a single writer.  This gateway accepts concurrent webhook
	// requests, so use one shared connection and wait briefly for a lock rather
	// than rejecting an otherwise valid alert with HTTP 500.
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)
	if _, err = db.Exec(`PRAGMA busy_timeout = 5000; PRAGMA journal_mode = WAL;`); err != nil {
		log.Fatal(err)
	}
	defer db.Close()
	if _, err = db.Exec(`CREATE TABLE IF NOT EXISTS events (
		id TEXT PRIMARY KEY, created_at INTEGER NOT NULL, source TEXT NOT NULL,
		severity TEXT NOT NULL, message TEXT NOT NULL, fingerprint TEXT NOT NULL,
		delivered_at INTEGER, attempts INTEGER NOT NULL DEFAULT 0, occurrences INTEGER NOT NULL DEFAULT 1, last_error TEXT
	); CREATE INDEX IF NOT EXISTS events_pending ON events(delivered_at, created_at);
	CREATE INDEX IF NOT EXISTS events_fingerprint ON events(fingerprint, created_at);`); err != nil {
		log.Fatal(err)
	}
	// Existing deployments created the table before occurrences existed.
	// Repeating this migration safely reports a duplicate-column error.
	_, _ = db.Exec(`ALTER TABLE events ADD COLUMN occurrences INTEGER NOT NULL DEFAULT 1`)
	a := &app{db: db, cfg: cfg}
	a.purgeExpired()
	go a.retryLoop()
	go a.retentionLoop()
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", a.health)
	mux.HandleFunc("POST /v1/events", a.receive)
	log.Printf("alert gateway listening on %s", cfg.addr)
	log.Fatal(http.ListenAndServe(cfg.addr, mux)) // #nosec G114 -- intentionally supervised by Docker
}

func (a *app) health(w http.ResponseWriter, _ *http.Request) {
	if err := a.db.Ping(); err != nil {
		http.Error(w, "database unavailable", http.StatusServiceUnavailable)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_, _ = io.WriteString(w, `{"status":"ok"}`)
}

func (a *app) receive(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxEventBytes))
	if err != nil {
		http.Error(w, "invalid body", http.StatusBadRequest)
		return
	}
	var input incomingEvent
	if json.Unmarshal(body, &input) != nil {
		input.Message = string(body)
	}
	input.Source = strings.TrimSpace(input.Source)
	input.Message = strings.TrimSpace(input.Message)
	if input.Message == "" {
		http.Error(w, "message is required", http.StatusBadRequest)
		return
	}
	context, contextErr := a.contextFor(input.Source, input.Message)
	if contextErr != nil {
		log.Printf("project configuration lookup failed: %v", contextErr)
		http.Error(w, "project configuration unavailable", http.StatusServiceUnavailable)
		return
	}
	if !context.Enabled {
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"status":"disabled"}`)
		return
	}
	e := event{ID: newID(), Source: input.Source, Message: truncate(input.Message, 4096), CreatedAt: time.Now().UTC().Unix()}
	e.Severity = severity(e.Message)
	e.Fingerprint = fingerprint(e.Source, e.Severity, e.Message)
	var duplicate string
	cooldown := a.cfg.cooldownSeconds
	if e.Severity == "warning" {
		cooldown = a.cfg.warningCooldownSeconds
	}
	if e.Severity == "critical" {
		cooldown = 0
	}
	err = a.db.QueryRow(`SELECT id FROM events WHERE fingerprint = ? AND created_at > ? ORDER BY created_at DESC LIMIT 1`, e.Fingerprint, e.CreatedAt-int64(cooldown)).Scan(&duplicate)
	if err == nil {
		_, _ = a.db.Exec(`UPDATE events SET occurrences = occurrences + 1 WHERE id = ?`, duplicate)
		w.Header().Set("Content-Type", "application/json")
		_, _ = fmt.Fprintf(w, `{"status":"duplicate","id":%q}`, duplicate)
		return
	}
	if !errors.Is(err, sql.ErrNoRows) {
		http.Error(w, "database error", http.StatusInternalServerError)
		return
	}
	if _, err = a.db.Exec(`INSERT INTO events (id, created_at, source, severity, message, fingerprint) VALUES (?, ?, ?, ?, ?, ?)`, e.ID, e.CreatedAt, e.Source, e.Severity, e.Message, e.Fingerprint); err != nil {
		http.Error(w, "database error", http.StatusInternalServerError)
		return
	}
	go a.deliver(e)
	w.Header().Set("Content-Type", "application/json")
	// Logtail treats only HTTP 200 as a successful webhook response. The event
	// is already durably queued above, so acknowledge it synchronously with 200
	// and continue Hermes delivery asynchronously.
	w.WriteHeader(http.StatusOK)
	_, _ = fmt.Fprintf(w, `{"status":"accepted","id":%q}`, e.ID)
}

func (a *app) retryLoop() {
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()
	for range ticker.C {
		rows, err := a.db.Query(`SELECT id, source, severity, message, fingerprint, created_at FROM events WHERE delivered_at IS NULL ORDER BY created_at LIMIT 50`)
		if err != nil {
			continue
		}
		var pending []event
		for rows.Next() {
			var e event
			if err := rows.Scan(&e.ID, &e.Source, &e.Severity, &e.Message, &e.Fingerprint, &e.CreatedAt); err == nil {
				pending = append(pending, e)
			}
		}
		_ = rows.Close()
		for _, e := range pending {
			a.deliver(e)
		}
	}
}

func (a *app) retentionLoop() {
	ticker := time.NewTicker(time.Hour)
	defer ticker.Stop()
	for range ticker.C {
		a.purgeExpired()
	}
}

func (a *app) purgeExpired() {
	cutoff := time.Now().UTC().Add(-time.Duration(a.cfg.retentionDays) * 24 * time.Hour).Unix()
	result, err := a.db.Exec(`DELETE FROM events WHERE created_at < ?`, cutoff)
	if err != nil {
		log.Printf("event retention cleanup failed: %v", err)
		return
	}
	if deleted, err := result.RowsAffected(); err == nil && deleted > 0 {
		log.Printf("event retention removed %d records older than %d days", deleted, a.cfg.retentionDays)
	}
}

func (a *app) deliver(e event) {
	context, err := a.contextFor(e.Source, e.Message)
	if err != nil {
		log.Printf("project configuration lookup failed while delivering %s: %v", e.ID, err)
		return
	}
	if !context.Enabled {
		_, _ = a.db.Exec(`UPDATE events SET delivered_at = ?, last_error = ? WHERE id = ?`, time.Now().UTC().Unix(), "disabled by project configuration", e.ID)
		return
	}
	payload, _ := json.Marshal(map[string]string{
		"incident_id": e.ID, "host": a.cfg.host, "source": formatSource(e.Source, context), "severity": e.Severity,
		"message": e.Message, "timestamp": time.Unix(e.CreatedAt, 0).UTC().Format(time.RFC3339),
	})
	timestamp := fmt.Sprintf("%d", time.Now().UTC().Unix())
	signed := e.ID + "." + timestamp + "." + string(payload)
	h := hmac.New(sha256.New, []byte(a.cfg.hermesSecret))
	_, _ = h.Write([]byte(signed))
	req, err := http.NewRequest(http.MethodPost, a.cfg.hermesURL, bytes.NewReader(payload))
	if err == nil {
		req.Header.Set("Content-Type", "application/json")
		// Hermes accepts Svix-compatible Standard Webhooks headers.
		req.Header.Set("svix-id", e.ID)
		req.Header.Set("svix-timestamp", timestamp)
		req.Header.Set("svix-signature", "v1,"+base64.StdEncoding.EncodeToString(h.Sum(nil)))
		client := &http.Client{Timeout: 10 * time.Second}
		response, callErr := client.Do(req)
		if callErr == nil && response.StatusCode >= 200 && response.StatusCode < 300 {
			_ = response.Body.Close()
			_, _ = a.db.Exec(`UPDATE events SET delivered_at = ?, attempts = attempts + 1, last_error = NULL WHERE id = ?`, time.Now().UTC().Unix(), e.ID)
			return
		}
		if response != nil {
			_ = response.Body.Close()
		}
		if callErr != nil {
			err = callErr
		} else {
			err = fmt.Errorf("unexpected Hermes response")
		}
	}
	_, _ = a.db.Exec(`UPDATE events SET attempts = attempts + 1, last_error = ? WHERE id = ?`, err.Error(), e.ID)
}

func loadProjectSettings(path string) (projectSettings, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return projectSettings{}, err
	}
	var settings projectSettings
	if err := json.Unmarshal(data, &settings); err != nil {
		return projectSettings{}, err
	}
	if len(settings.Projects) == 0 {
		return projectSettings{}, errors.New("projects must not be empty")
	}
	return settings, nil
}

func (a *app) contextFor(source, message string) (logContext, error) {
	settings, err := loadProjectSettings(a.cfg.projectsFile)
	if err != nil {
		return logContext{}, err
	}
	program := programFromSource(source)
	context := logContext{Program: program, LogFile: source}
	if dll := dllLike.FindString(message); dll != "" {
		context.DLL = dll
	}
	for project, definition := range settings.Projects {
		if enabled, found := definition.Programs[program]; found {
			context.Project = project
			context.Enabled = enabled
			return context, nil
		}
	}
	return context, nil
}

func programFromSource(source string) string {
	const logsPrefix = "/logs/"
	clean := filepath.Clean(source)
	if strings.HasPrefix(clean, logsPrefix) {
		relative := strings.TrimPrefix(clean, logsPrefix)
		if program, _, found := strings.Cut(relative, "/"); found {
			return program
		}
	}
	return "unknown"
}

func formatSource(source string, context logContext) string {
	dll := context.DLL
	if dll == "" {
		dll = "unknown"
	}
	return fmt.Sprintf("project=%s; program=%s; log_file=%s; dll=%s", context.Project, context.Program, context.LogFile, dll)
}

func severity(message string) string {
	u := strings.ToUpper(message)
	switch {
	case strings.Contains(u, "FATAL"), strings.Contains(u, "PANIC"), strings.Contains(u, "OUTOFMEMORY"):
		return "critical"
	case strings.Contains(u, "ERROR"), strings.Contains(u, "EXCEPTION"):
		return "error"
	case strings.Contains(u, "WARN"):
		return "warning"
	default:
		return "unknown"
	}
}

func fingerprint(source, level, message string) string {
	canonical := strings.ToLower(message)
	canonical = uuidLike.ReplaceAllString(canonical, "<uuid>")
	canonical = timestampLike.ReplaceAllString(canonical, "<timestamp>")
	canonical = volatileField.ReplaceAllString(canonical, "$1<value>")
	canonical = longNumber.ReplaceAllString(canonical, "<n>")
	h := sha256.Sum256([]byte(source + "\x00" + level + "\x00" + canonical))
	return hex.EncodeToString(h[:])
}

func newID() string { b := make([]byte, 16); _, _ = rand.Read(b); return hex.EncodeToString(b) }
func envOr(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}

func envPositiveInt(key string, fallback int) int {
	if value, err := strconv.Atoi(os.Getenv(key)); err == nil && value > 0 {
		return value
	}
	return fallback
}

func truncate(value string, max int) string {
	if len(value) <= max {
		return value
	}
	return value[:max]
}
