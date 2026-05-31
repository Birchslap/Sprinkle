/**
 * sprinkle.js
 *
 * Client for the Sprinkle DM engine.
 * Handles: campaign launcher, WebSocket protocol, typewriter buffer,
 * dialogue detection, tab switching, text size, high contrast.
 */

// ============================================================
// Config
// ============================================================

const TYPEWRITER = {
    bufferMs: 1500,         // Initial buffer before text starts appearing
    charMs: 12,             // Milliseconds per character during drip
    cursorLingerMs: 600,    // How long the cursor stays after text finishes
};

// ============================================================
// State
// ============================================================

let ws = null;
let campaignId = null;
let isNewCampaign = false;
let highContrast = false;

// Typewriter state
let typewriterQueue = "";
let typewriterActive = false;
let typewriterTimer = null;
let bufferTimer = null;
let currentDmBlock = null;
let turnInProgress = false;

// ============================================================
// DOM References
// ============================================================

const launcher = document.getElementById("launcher");
const campaignList = document.getElementById("campaign-list");
const newCampaignName = document.getElementById("new-campaign-name");
const newCampaignSetting = document.getElementById("new-campaign-setting");
const newCampaignCharacter = document.getElementById("new-campaign-character");
const newCampaignBtn = document.getElementById("new-campaign-btn");
const campaignDisplay = document.getElementById("campaign-display");

const chatWindow = document.getElementById("chat-window");
const playerInput = document.getElementById("player-input");
const sendBtn = document.getElementById("send-btn");

const fontSizeSelect = document.getElementById("font-size-select");
const contrastToggle = document.getElementById("contrast-toggle");

const chatTabs = document.querySelectorAll(".chat-tab");
const stateTabs = document.querySelectorAll(".state-tab");

// ============================================================
// Campaign Launcher
// ============================================================

async function loadCampaigns() {
    try {
        const res = await fetch("/api/campaigns");
        const data = await res.json();
        const campaigns = data.campaigns || [];
        campaignList.innerHTML = "";

        if (campaigns.length === 0) {
            campaignList.innerHTML = '<li class="campaign-empty">No campaigns yet.</li>';
            return;
        }

        campaigns.forEach(c => {
            const li = document.createElement("li");
            li.className = "campaign-item";

            const info = document.createElement("div");
            info.className = "campaign-item-info";
            info.textContent = c.name;
            if (c.setting) {
                const span = document.createElement("span");
                span.className = "campaign-setting";
                span.textContent = ` — ${c.setting}`;
                info.appendChild(span);
            }
            info.addEventListener("click", () => resumeCampaign(c.id, c.name));
            li.appendChild(info);

            const actions = document.createElement("div");
            actions.className = "campaign-actions";

            const chatExport = document.createElement("button");
            chatExport.className = "campaign-export";
            chatExport.textContent = "📜";
            chatExport.title = "Download chat transcript";
            chatExport.addEventListener("click", (e) => {
                e.stopPropagation();
                window.open(`/api/campaigns/${c.id}/chat.md`);
            });
            actions.appendChild(chatExport);

            const usageExport = document.createElement("button");
            usageExport.className = "campaign-export";
            usageExport.textContent = "📊";
            usageExport.title = "Download token usage CSV";
            usageExport.addEventListener("click", (e) => {
                e.stopPropagation();
                window.open(`/api/campaigns/${c.id}/usage.csv`);
            });
            actions.appendChild(usageExport);

            const del = document.createElement("button");
            del.className = "campaign-delete";
            del.textContent = "✕";
            del.title = "Delete campaign";
            del.addEventListener("click", (e) => {
                e.stopPropagation();
                deleteCampaign(c.id, c.name);
            });
            actions.appendChild(del);

            li.appendChild(actions);

            campaignList.appendChild(li);
        });
    } catch (err) {
        campaignList.innerHTML = '<li class="campaign-empty">Failed to load campaigns.</li>';
    }
}

async function createCampaign() {
    const name = newCampaignName.value.trim();
    if (!name) {
        newCampaignName.focus();
        return;
    }

    try {
        const charDoc = newCampaignCharacter.value.trim() || null;
        const res = await fetch("/api/campaigns", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                name: name,
                setting: newCampaignSetting.value.trim() || null,
                character_doc: charDoc,
            }),
        });
        const data = await res.json();
        isNewCampaign = true;
        startGame(data.campaign.id, data.campaign.name);
    } catch (err) {
        console.error("Failed to create campaign:", err);
    }
}

function resumeCampaign(id, name) {
    isNewCampaign = false;
    startGame(id, name);
}

async function deleteCampaign(id, name) {
    if (!confirm(`Delete "${name}"? This cannot be undone.`)) return;

    try {
        const res = await fetch(`/api/campaigns/${id}`, { method: "DELETE" });
        if (res.ok) {
            loadCampaigns(); // Refresh the list.
        } else {
            console.error("Failed to delete campaign.");
        }
    } catch (err) {
        console.error("Failed to delete campaign:", err);
    }
}

newCampaignBtn.addEventListener("click", createCampaign);
newCampaignName.addEventListener("keydown", e => {
    if (e.key === "Enter") createCampaign();
});

// ============================================================
// Game Connection
// ============================================================

function startGame(id, name) {
    campaignId = id;
    campaignDisplay.textContent = name;
    launcher.classList.add("hidden");
    chatWindow.innerHTML = "";
    connectWebSocket();
}

function connectWebSocket() {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${protocol}//${location.host}/ws/game/${campaignId}`);

    ws.onopen = () => {
        enableInput();
        ws.send(JSON.stringify({
            type: isNewCampaign ? "start" : "resume",
        }));
    };

    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        handleServerMessage(msg);
    };

    ws.onclose = () => {
        disableInput();
        appendSystem("Connection lost. Refresh to reconnect.");
    };

    ws.onerror = () => {
        disableInput();
    };
}

// ============================================================
// Message Handling
// ============================================================

function handleServerMessage(msg) {
    switch (msg.type) {
        case "history":
            renderHistoryMessage(msg.role, msg.content);
            break;
        case "ready":
            if (isNewCampaign) {
                // Automatically trigger the DM's opening narration.
                ws.send(JSON.stringify({ type: "message", content: "[BEGIN CAMPAIGN]" }));
                isNewCampaign = false;
                disableInput(); // Wait for DM response.
            }
            break;
        case "history_end":
            scrollToBottom();
            enableInput();
            break;
        case "delta":
            handleDelta(msg.text);
            break;
        case "turn_end":
            handleTurnEnd();
            break;
        case "error":
            appendSystem(`Error: ${msg.message}`);
            enableInput();
            break;
    }
}

function renderHistoryMessage(role, content) {
    if (role === "user") {
        appendPlayerMessage(content);
    } else if (role === "assistant") {
        const div = createMessageBlock("dm");
        const container = document.createElement("div");
        container.className = "message-text";
        container.textContent = content;
        container.innerHTML = highlightDialogue(container.innerHTML);
        div.appendChild(container);
        div.classList.remove("typing");
    }
}

function handleDelta(text) {
    if (!turnInProgress) {
        turnInProgress = true;
        currentDmBlock = createMessageBlock("dm");
        disableInput();
    }
    typewriterQueue += text;
    startTypewriter();
}

function handleTurnEnd() {
    // Flush remaining queue immediately at normal typewriter speed.
    // The typewriter will finish naturally — we just stop accepting new deltas.
    turnInProgress = false;

    // If nothing was ever queued (pure tool-call turn), clean up.
    if (!typewriterQueue && !typewriterActive) {
        enableInput();
    }
}

// ============================================================
// Typewriter
// ============================================================

function startTypewriter() {
    if (typewriterActive) return; // Already dripping.

    if (!bufferTimer) {
        // Start the initial buffer period.
        bufferTimer = setTimeout(() => {
            bufferTimer = null;
            typewriterActive = true;
            dripNext();
        }, TYPEWRITER.bufferMs);
    }
}

function dripNext() {
    if (typewriterQueue.length === 0) {
        // Queue empty.
        typewriterActive = false;

        if (!turnInProgress) {
            // Turn is over and queue is drained — finalise.
            setTimeout(() => finaliseDmBlock(), TYPEWRITER.cursorLingerMs);
        }
        // If turn still in progress, we'll resume when the next delta arrives.
        return;
    }

    const char = typewriterQueue[0];
    typewriterQueue = typewriterQueue.slice(1);
    appendCharToBlock(char);

    typewriterTimer = setTimeout(dripNext, TYPEWRITER.charMs);
}

function finaliseDmBlock() {
    if (!currentDmBlock) return;

    // Process the completed text for dialogue highlighting.
    const container = currentDmBlock.querySelector(".message-text");
    if (container) {
        container.innerHTML = highlightDialogue(container.innerHTML);
    }
    currentDmBlock.classList.remove("typing");
    currentDmBlock = null;
    scrollToBottom();
    enableInput();
}

function appendCharToBlock(char) {
    if (!currentDmBlock) return;

    // Append to a text container, not directly to the block
    // (speaker label is the first child).
    let container = currentDmBlock.querySelector(".message-text");
    if (!container) {
        container = document.createElement("div");
        container.className = "message-text";
        currentDmBlock.appendChild(container);
    }

    if (char === "\n") {
        container.appendChild(document.createElement("br"));
    } else {
        container.appendChild(document.createTextNode(char));
    }
    scrollToBottom();
}

// ============================================================
// Dialogue Detection
// ============================================================

function highlightDialogue(html) {
    // Find quoted speech and wrap in dialogue spans.
    // Handles "straight quotes" and \u201C\u201D curly quotes.
    return html.replace(
        /(["\u201C])([^"\u201D]*?)(["\u201D])/g,
        '<span class="dialogue dialogue-npc">\u201C$2\u201D</span>'
    );
}

// ============================================================
// Message Blocks
// ============================================================

function createMessageBlock(role) {
    const div = document.createElement("div");
    div.className = `message ${role}`;

    // Speaker label.
    const label = document.createElement("div");
    label.className = `speaker-label label-${role}`;
    label.textContent = role === "dm" ? "Sprinkle" : "Player";
    div.appendChild(label);

    if (role === "dm") {
        div.classList.add("typing"); // Shows cursor via CSS ::after.
    }
    chatWindow.appendChild(div);
    scrollToBottom();
    return div;
}

function appendPlayerMessage(text) {
    const div = createMessageBlock("player");
    const container = document.createElement("div");
    container.className = "message-text";
    container.textContent = text;
    div.appendChild(container);
    return div;
}

function appendSystem(text) {
    const div = document.createElement("div");
    div.className = "message system";
    div.textContent = text;
    chatWindow.appendChild(div);
    scrollToBottom();
}

// ============================================================
// Mechanical Results (dice rolls, checks)
// ============================================================

function appendMechanical(data) {
    // Future: parse tool results from the server and display
    // styled mechanical blocks (rolls, damage, checks).
    const div = document.createElement("div");
    div.className = "mechanical-block";
    div.textContent = data;
    chatWindow.appendChild(div);
    scrollToBottom();
}

// ============================================================
// Player Input
// ============================================================

function sendMessage() {
    const text = playerInput.value.trim();
    if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;

    appendPlayerMessage(text);
    ws.send(JSON.stringify({ type: "message", content: text }));
    playerInput.value = "";
    disableInput();
}

function enableInput() {
    playerInput.disabled = false;
    sendBtn.disabled = false;
    playerInput.focus();
}

function disableInput() {
    playerInput.disabled = true;
    sendBtn.disabled = true;
}

sendBtn.addEventListener("click", sendMessage);
playerInput.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

// ============================================================
// Tabs
// ============================================================

chatTabs.forEach(tab => {
    tab.addEventListener("click", () => {
        chatTabs.forEach(t => t.classList.remove("active"));
        tab.classList.add("active");
        // Future: switch between IC and OOC message streams.
    });
});

stateTabs.forEach(tab => {
    tab.addEventListener("click", () => {
        stateTabs.forEach(t => t.classList.remove("active"));
        tab.classList.add("active");

        const page = tab.dataset.page;
        document.querySelectorAll(".state-page").forEach(p => p.classList.remove("active"));
        document.getElementById(`page-${page}`).classList.add("active");
    });
});

// ============================================================
// Text Size
// ============================================================

const SIZE_CLASSES = { "15": "text-small", "17": "text-medium", "19": "text-large" };

function applyTextSize(value) {
    Object.values(SIZE_CLASSES).forEach(cls => chatWindow.classList.remove(cls));
    const cls = SIZE_CLASSES[value];
    if (cls) chatWindow.classList.add(cls);
}

fontSizeSelect.addEventListener("change", () => applyTextSize(fontSizeSelect.value));

// Apply default on load.
applyTextSize(fontSizeSelect.value);

// ============================================================
// High Contrast
// ============================================================

contrastToggle.addEventListener("click", () => {
    highContrast = !highContrast;
    contrastToggle.classList.toggle("active", highContrast);
    document.body.classList.toggle("high-contrast", highContrast);
});

// ============================================================
// Map Controls
// ============================================================

const swapViewsBtn = document.getElementById("swap-views");
const popoutMapBtn = document.getElementById("popout-map");
const leftPanel = document.getElementById("left-panel");
const rightPanel = document.getElementById("right-panel");
let mapExpanded = false;
let mapWindow = null;

swapViewsBtn.addEventListener("click", () => {
    mapExpanded = !mapExpanded;
    document.body.classList.toggle("map-expanded", mapExpanded);
    swapViewsBtn.textContent = mapExpanded ? "⇄ Collapse" : "⇄ Expand";
});

popoutMapBtn.addEventListener("click", () => {
    if (mapWindow && !mapWindow.closed) {
        mapWindow.focus();
        return;
    }

    const mapContainer = document.getElementById("map-container");
    const mapContent = mapContainer.innerHTML;

    mapWindow = window.open("", "SprinkleMap", "width=900,height=700");
    mapWindow.document.write(`
        <!DOCTYPE html>
        <html><head>
        <title>Sprinkle — Map</title>
        <style>
            body {
                margin: 0; padding: 0;
                background: #0e0e16;
                display: flex; align-items: center; justify-content: center;
                height: 100vh; overflow: hidden;
            }
            #map-container {
                width: 100%; height: 100%;
                display: flex; align-items: center; justify-content: center;
            }
            #map-container img {
                max-width: 100%; max-height: 100%; object-fit: contain;
            }
            #map-placeholder {
                color: #555; font-family: Inter, sans-serif; font-size: 14px;
            }
            canvas { width: 100%; height: 100%; }
        </style>
        </head><body>
        <div id="map-container">${mapContent}</div>
        </body></html>
    `);
    mapWindow.document.close();
});

// ============================================================
// Notes (local player notes)
// ============================================================

const noteInput = document.getElementById("note-input");
const noteAddBtn = document.getElementById("note-add-btn");
const notesList = document.getElementById("notes-list");

function addNote() {
    const text = noteInput.value.trim();
    if (!text) return;

    const div = document.createElement("div");
    div.className = "note-entry";
    div.textContent = text;

    // Click to remove.
    div.addEventListener("click", () => div.remove());
    notesList.appendChild(div);
    noteInput.value = "";
}

noteAddBtn.addEventListener("click", addNote);
noteInput.addEventListener("keydown", e => {
    if (e.key === "Enter") addNote();
});

// ============================================================
// Utilities
// ============================================================

function scrollToBottom() {
    chatWindow.scrollTop = chatWindow.scrollHeight;
}

// ============================================================
// Init
// ============================================================

loadCampaigns();
