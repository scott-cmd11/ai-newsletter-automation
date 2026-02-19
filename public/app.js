/* ============================================
   AI This Week — Frontend Logic
   ============================================ */

const SECTIONS = [
    { key: "trending", label: "Trending AI" },
    { key: "canadian", label: "Canadian News" },
    { key: "global", label: "Global News" },
    { key: "events", label: "Events" },
    { key: "events_public", label: "Public-Servant Events" },
    { key: "agri", label: "Grain / Agri-Tech" },
    { key: "ai_progress", label: "AI Progress" },
    { key: "research_plain", label: "Plain-Language Research" },
    { key: "deep_dive", label: "Deep Dive" },
];

let generatedHTML = null;
let _startTime = null;
let _timerInterval = null;
let _sectionTimes = [];   // track how long each section takes

/* ── Helpers ────────────────────────────── */

function $(id) { return document.getElementById(id); }

function setVisible(id, show) {
    $(id).style.display = show ? "" : "none";
}

function setChipState(key, state) {
    const chip = document.querySelector(`[data-key="${key}"]`);
    if (!chip) return;
    chip.className = "section-chip " + state;

    const iconEl = chip.querySelector(".chip-icon");
    if (state === "active") {
        iconEl.innerHTML = '<div class="spinner"></div>';
    } else if (state === "done") {
        iconEl.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20,6 9,17 4,12"/></svg>';
    } else if (state === "error") {
        iconEl.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
    }
}

/* ── Progress UI ────────────────────────── */

function initProgress() {
    const grid = $("section-status");
    grid.innerHTML = SECTIONS.map(s =>
        `<div class="section-chip" data-key="${s.key}">
            <span class="chip-icon">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" opacity="0.4"><circle cx="12" cy="12" r="10"/></svg>
            </span>
            <span>${s.label}</span>
        </div>`
    ).join("");

    $("progress-bar").style.width = "0%";
    $("progress-count").textContent = `0 / ${SECTIONS.length}`;
    $("progress-text").textContent = "Starting generation…";
    $("progress-time").textContent = "Est. 3–5 min";
    setVisible("progress-container", true);
    setVisible("error-container", false);
    setVisible("result-container", false);

    // Start elapsed timer
    _startTime = Date.now();
    _sectionTimes = [];
    if (_timerInterval) clearInterval(_timerInterval);
    _timerInterval = setInterval(_updateTimer, 1000);
}

function _formatTime(ms) {
    const totalSec = Math.floor(ms / 1000);
    const m = Math.floor(totalSec / 60);
    const s = totalSec % 60;
    return `${m}:${s.toString().padStart(2, "0")}`;
}

function _updateTimer() {
    if (!_startTime) return;
    const elapsed = Date.now() - _startTime;
    const done = _sectionTimes.length;
    const remaining = SECTIONS.length - done;

    let timeText = `Elapsed: ${_formatTime(elapsed)}`;
    if (done > 0 && remaining > 0) {
        const avgPerSection = elapsed / done;
        const estRemaining = avgPerSection * remaining;
        timeText += ` · ~${_formatTime(estRemaining)} remaining`;
    } else if (remaining === 0) {
        timeText = `Completed in ${_formatTime(elapsed)}`;
        if (_timerInterval) { clearInterval(_timerInterval); _timerInterval = null; }
    }
    $("progress-time").textContent = timeText;
}

function updateProgress(completed, currentLabel) {
    const pct = (completed / SECTIONS.length) * 100;
    $("progress-bar").style.width = pct + "%";
    $("progress-count").textContent = `${completed} / ${SECTIONS.length}`;
    $("progress-text").textContent = currentLabel
        ? `Generating: ${currentLabel}…`
        : "Complete!";
}

/* ── Generate ───────────────────────────── */

async function generateNewsletter() {
    const days = parseInt($("days-input").value) || 7;
    const lang = $("lang-select").value || "en";
    const generateBtn = $("generate-btn");
    const downloadBtn = $("download-btn");

    generateBtn.disabled = true;
    downloadBtn.disabled = true;
    generatedHTML = null;

    initProgress();

    const allSections = {};
    let completed = 0;
    let hasErrors = false;
    let _sectionStart = Date.now();

    for (const section of SECTIONS) {
        setChipState(section.key, "active");
        updateProgress(completed, section.label);

        try {
            const resp = await fetch(`/api/generate_section?key=${section.key}&days=${days}&lang=${lang}`);
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({ error: resp.statusText }));
                throw new Error(err.error || `HTTP ${resp.status}`);
            }

            const data = await resp.json();
            allSections[section.key] = data.items || [];

            if (data.warning || (data.items && data.items.length === 0)) {
                // Section returned but had issues (rate limit, no content)
                setChipState(section.key, "done");
                if (data.warning) console.warn(`Section ${section.key} warning:`, data.warning);
            } else {
                setChipState(section.key, "done");
            }
        } catch (err) {
            console.error(`Section ${section.key} failed:`, err);
            setChipState(section.key, "error");
            allSections[section.key] = [];
            hasErrors = true;
        }

        completed++;
        _sectionTimes.push(Date.now() - _sectionStart);
        _sectionStart = Date.now();
        _updateTimer();
        updateProgress(completed, completed < SECTIONS.length ? SECTIONS[completed]?.label : null);
    }

    // Render the full newsletter
    $("progress-text").textContent = "Rendering newsletter…";

    try {
        const today = new Date().toISOString().slice(0, 10);

        // Collect top-relevance items for TL;DR
        const allItems = Object.values(allSections).flat();
        allItems.sort((a, b) => (b.Relevance || 0) - (a.Relevance || 0));
        const topItems = allItems.slice(0, 6);

        // Generate TL;DR via server
        let tldr = [];
        if (topItems.length > 0) {
            try {
                const tldrResp = await fetch("/api/tldr", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ items: topItems, lang: lang }),
                });
                if (tldrResp.ok) {
                    const tldrData = await tldrResp.json();
                    tldr = tldrData.tldr || [];
                }
            } catch (e) {
                console.warn("TL;DR generation failed, continuing without it", e);
            }
        }

        const renderResp = await fetch("/api/render", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ sections: allSections, run_date: today, tldr: tldr, lang: lang }),
        });

        if (!renderResp.ok) {
            throw new Error(`Render failed: HTTP ${renderResp.status}`);
        }

        generatedHTML = await renderResp.text();

        // Display in iframe
        const frame = $("newsletter-frame");
        frame.srcdoc = generatedHTML;
        $("result-date").textContent = today;
        setVisible("result-container", true);
        downloadBtn.disabled = false;

        updateProgress(SECTIONS.length, null);
        $("progress-text").textContent = hasErrors
            ? "Done with some errors — check highlighted sections"
            : "✓ Newsletter generated successfully!";

    } catch (err) {
        $("error-text").textContent = `Render error: ${err.message}`;
        setVisible("error-container", true);
    }

    generateBtn.disabled = false;
}

/* ── Download ───────────────────────────── */

function downloadHTML() {
    if (!generatedHTML) return;

    const blob = new Blob([generatedHTML], { type: "text/html" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `ai-this-week-${new Date().toISOString().slice(0, 10)}.html`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}
