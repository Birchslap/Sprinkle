// =============================================
// FONT SIZE CONTROL
// =============================================
const fontSizeSelect = document.getElementById('font-size-select');

fontSizeSelect.addEventListener('change', function() {
    const size = this.value + 'px';
    document.documentElement.style.setProperty('--size-narrative', size);
});

// =============================================
// HIGH CONTRAST TOGGLE
// =============================================
const contrastToggle = document.getElementById('contrast-toggle');

contrastToggle.addEventListener('click', function() {
    this.classList.toggle('active');
    document.body.classList.toggle('high-contrast');
});

// =============================================
// MODEL SELECTOR (dev tool)
// =============================================
const modelSelect = document.getElementById('model-select');
const modelStatus = document.getElementById('model-status');

async function loadModels() {
    try {
        const resp = await fetch('/api/models');
        const data = await resp.json();
        modelSelect.innerHTML = '';
        for (const [key, name] of Object.entries(data.models)) {
            const opt = document.createElement('option');
            opt.value = key;
            opt.textContent = name;
            if (key === data.active) opt.selected = true;
            modelSelect.appendChild(opt);
        }
        modelStatus.textContent = '';
    } catch (e) {
        // Running without backend — that's fine for design work
        const opt = document.createElement('option');
        opt.textContent = 'No backend';
        modelSelect.appendChild(opt);
    }
}

modelSelect.addEventListener('change', async function() {
    const key = this.value;
    modelStatus.textContent = 'Switching...';
    try {
        const resp = await fetch(`/api/model/${key}`, { method: 'POST' });
        const data = await resp.json();
        if (data.error) {
            modelStatus.textContent = data.error;
        } else {
            modelStatus.textContent = `Active: ${data.name}`;
            setTimeout(() => { modelStatus.textContent = ''; }, 2000);
        }
    } catch (e) {
        modelStatus.textContent = '';
    }
});

loadModels();

// =============================================
// WEBSOCKET (graceful when no backend)
// =============================================
let ws = null;
const chatWindow = document.getElementById('chat-window');
let activeChat = 'ic';

function connectWebSocket() {
    try {
        ws = new WebSocket(`ws://${window.location.host}/ws`);

        ws.onmessage = function(event) {
            const data = JSON.parse(event.data);
            addMessage(data.type, data.content);
        };

        ws.onclose = function() {
            // Silent when running without backend
        };
    } catch (e) {
        // No backend — design mode
    }
}

// Only connect if served from a server (not file://)
if (window.location.protocol !== 'file:') {
    connectWebSocket();
}

// =============================================
// CHAT FUNCTIONS
// =============================================
function addMessage(type, content) {
    // Remove welcome scene on first real interaction
    const welcome = chatWindow.querySelector('.welcome-scene');

    const div = document.createElement('div');
    div.classList.add('message', type);
    div.textContent = content;
    chatWindow.appendChild(div);
    chatWindow.scrollTop = chatWindow.scrollHeight;
}

function sendMessage() {
    const input = document.getElementById('player-input');
    const message = input.value.trim();
    if (!message) return;

    addMessage('player', message);
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ message: message, tab: activeChat }));
    }
    input.value = '';
}

document.getElementById('player-input').addEventListener('keydown', function(e) {
    if (e.key === 'Enter') sendMessage();
});

document.getElementById('send-btn').addEventListener('click', sendMessage);

// =============================================
// CHAT TAB SWITCHING
// =============================================
document.querySelectorAll('.chat-tab').forEach(tab => {
    tab.addEventListener('click', function() {
        document.querySelectorAll('.chat-tab').forEach(t => t.classList.remove('active'));
        this.classList.add('active');
        activeChat = this.dataset.tab;
    });
});

// =============================================
// STATE PANEL TAB SWITCHING
// =============================================
document.querySelectorAll('.state-tab').forEach(tab => {
    tab.addEventListener('click', function() {
        const targetPage = this.dataset.page;

        // Update active tab styling
        document.querySelectorAll('.state-tab').forEach(t => t.classList.remove('active'));
        this.classList.add('active');

        if (mapInMain) {
            // Map is expanded — right panel holds chat + tab content
            const chatWindow = document.getElementById('chat-window');

            // Hide all state pages in right panel
            document.querySelectorAll('#state-content .state-page').forEach(p => {
                p.classList.remove('active');
                p.style.display = 'none';
            });

            if (targetPage === 'map') {
                // Show chat log, hide other content
                chatWindow.style.display = '';
            } else {
                // Hide chat log, show selected tab
                chatWindow.style.display = 'none';
                const page = document.getElementById('page-' + targetPage);
                if (page) {
                    page.classList.add('active');
                    page.style.display = 'block';
                }
            }
        } else {
            // Normal mode — standard tab switching
            document.querySelectorAll('.state-page').forEach(p => p.classList.remove('active'));
            document.getElementById('page-' + targetPage).classList.add('active');
        }
    });
});

// =============================================
// NOTES
// =============================================
let notes = [];

function renderNotes() {
    const list = document.getElementById('notes-list');
    list.innerHTML = '';
    notes.forEach((note, index) => {
        const div = document.createElement('div');
        div.classList.add('note-entry');
        div.innerHTML = `
            <div class="note-time">${note.time}</div>
            <div>${note.text}</div>
            <span class="note-delete" data-index="${index}">&times;</span>
        `;
        list.appendChild(div);
    });

    document.querySelectorAll('.note-delete').forEach(btn => {
        btn.addEventListener('click', function() {
            notes.splice(parseInt(this.dataset.index), 1);
            renderNotes();
        });
    });
}

function addNote() {
    const input = document.getElementById('note-input');
    const text = input.value.trim();
    if (!text) return;

    const now = new Date();
    const time = now.toLocaleString();
    notes.push({ text: text, time: time });
    input.value = '';
    renderNotes();
}

document.getElementById('note-add-btn').addEventListener('click', addNote);
document.getElementById('note-input').addEventListener('keydown', function(e) {
    if (e.key === 'Enter') addNote();
});

// =============================================
// MAP/CHAT VIEW SWAPPING
// =============================================
let mapInMain = false;

document.getElementById('swap-views').addEventListener('click', function() {
    const mainView = document.getElementById('main-view');
    const rightPanel = document.getElementById('right-panel');
    const stateContent = document.getElementById('state-content');
    const chatWindow = document.getElementById('chat-window');
    const mapPage = document.getElementById('page-map');
    const swapBtn = document.getElementById('swap-views');

    if (!mapInMain) {
        // Move map page to main view, chat to right panel
        mainView.insertBefore(mapPage, mainView.firstChild);
        mapPage.classList.add('active');
        mapPage.style.display = 'flex';
        mapPage.style.flexDirection = 'column';
        mapPage.style.flex = '1';
        stateContent.insertBefore(chatWindow, stateContent.firstChild);
        chatWindow.style.flex = '1';
        swapBtn.textContent = '⇄ Collapse';
        mapInMain = true;
    } else {
        // Move chat back to main, map back to right panel tab
        mainView.insertBefore(chatWindow, mainView.firstChild);
        chatWindow.style.flex = '';
        chatWindow.style.display = '';
        stateContent.insertBefore(mapPage, stateContent.firstChild);
        mapPage.style.display = '';
        mapPage.style.flexDirection = '';
        mapPage.style.flex = '';
        swapBtn.textContent = '⇄ Expand';
        mapInMain = false;
        // Re-activate map tab view, reset all page displays
        document.querySelectorAll('.state-page').forEach(p => {
            p.classList.remove('active');
            p.style.display = '';
        });
        mapPage.classList.add('active');
    }

    chatWindow.scrollTop = chatWindow.scrollHeight;
});

// =============================================
// MAP RENDERER
// =============================================
class MapRenderer {
    constructor(canvas) {
        this.canvas = canvas;
        this.ctx = canvas.getContext('2d');
        this.cellSize = 32;
        this.mapData = null;
        this.offsetX = 0;
        this.offsetY = 0;
        this.isDragging = false;
        this.dragStartX = 0;
        this.dragStartY = 0;
        this.scrollOffsetX = 0;
        this.scrollOffsetY = 0;

        // Colours
        this.colours = {
            floor: '#1a1a25',
            wall: '#2a2a35',
            wallTop: '#353545',
            grid: 'rgba(42, 90, 58, 0.15)',
            door: '#bba87e',
            doorFrame: '#665a3e',
            token: '#2a5a3a',
            tokenBorder: '#4a8a5a',
            tokenLabel: '#e8e8e8',
            water: 'rgba(50, 80, 120, 0.4)',
            background: '#080810'
        };

        this.setupEvents();
    }

    setupEvents() {
        // Pan by dragging
        this.canvas.addEventListener('mousedown', (e) => {
            this.isDragging = true;
            this.dragStartX = e.clientX - this.scrollOffsetX;
            this.dragStartY = e.clientY - this.scrollOffsetY;
            this.canvas.style.cursor = 'grabbing';
        });

        window.addEventListener('mousemove', (e) => {
            if (!this.isDragging) return;
            this.scrollOffsetX = e.clientX - this.dragStartX;
            this.scrollOffsetY = e.clientY - this.dragStartY;
            this.render();
        });

        window.addEventListener('mouseup', () => {
            this.isDragging = false;
            this.canvas.style.cursor = 'grab';
        });

        // Zoom with scroll wheel
        this.canvas.addEventListener('wheel', (e) => {
            e.preventDefault();
            const oldSize = this.cellSize;
            const delta = e.deltaY > 0 ? -2 : 2;
            this.cellSize = Math.max(16, Math.min(64, this.cellSize + delta));

            // Zoom toward mouse position
            const rect = this.canvas.getBoundingClientRect();
            const mouseX = e.clientX - rect.left;
            const mouseY = e.clientY - rect.top;
            const scale = this.cellSize / oldSize;
            this.scrollOffsetX = mouseX - (mouseX - this.scrollOffsetX) * scale;
            this.scrollOffsetY = mouseY - (mouseY - this.scrollOffsetY) * scale;

            this.render();
        }, { passive: false });

        this.canvas.style.cursor = 'grab';
    }

    loadMap(mapData) {
        this.mapData = mapData;
        this.fitToContainer();
        this.render();
    }

    fitToContainer() {
        if (!this.mapData) return;
        const container = this.canvas.parentElement;
        const padW = 20, padH = 20;
        const availW = container.clientWidth - padW;
        const availH = container.clientHeight - padH;

        // Guard against zero-dimension containers (tab not visible)
        if (availW <= 0 || availH <= 0) {
            this.cellSize = 32;
            return;
        }

        const fitW = availW / this.mapData.width;
        const fitH = availH / this.mapData.height;
        this.cellSize = Math.max(12, Math.min(64, Math.floor(Math.min(fitW, fitH))));
        const mapPixelW = this.mapData.width * this.cellSize;
        const mapPixelH = this.mapData.height * this.cellSize;
        this.scrollOffsetX = (this.canvas.width - mapPixelW) / 2;
        this.scrollOffsetY = (this.canvas.height - mapPixelH) / 2;
    }

    resize() {
        const container = this.canvas.parentElement;
        const w = container.clientWidth;
        const h = container.clientHeight;

        // Don't resize to zero
        if (w <= 0 || h <= 0) return;

        this.canvas.width = w;
        this.canvas.height = h;
        if (this.mapData) {
            this.fitToContainer();
            this.render();
        }
    }

    render() {
        if (!this.mapData) return;
        const ctx = this.ctx;
        const cs = this.cellSize;
        const map = this.mapData;
        const ox = this.scrollOffsetX;
        const oy = this.scrollOffsetY;

        // Clear
        ctx.fillStyle = this.colours.background;
        ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);

        // Draw floor tiles
        for (let y = 0; y < map.height; y++) {
            for (let x = 0; x < map.width; x++) {
                const cell = map.cells[y][x];
                const px = ox + x * cs;
                const py = oy + y * cs;

                if (cell === 0) continue; // void

                // Floor
                if (cell === 1 || cell === 3 || cell === 4) {
                    ctx.fillStyle = this.colours.floor;
                    ctx.fillRect(px, py, cs, cs);
                }

                // Water
                if (cell === 4) {
                    ctx.fillStyle = this.colours.water;
                    ctx.fillRect(px, py, cs, cs);
                }

                // Wall
                if (cell === 2) {
                    ctx.fillStyle = this.colours.wall;
                    ctx.fillRect(px, py, cs, cs);
                    // Top face highlight
                    ctx.fillStyle = this.colours.wallTop;
                    ctx.fillRect(px, py, cs, cs * 0.3);
                }

                // Door
                if (cell === 3) {
                    const doorInset = cs * 0.3;
                    ctx.fillStyle = this.colours.doorFrame;
                    ctx.fillRect(px + doorInset, py, cs - doorInset * 2, cs);
                    ctx.fillStyle = this.colours.door;
                    ctx.fillRect(px + doorInset + 2, py + 2, cs - doorInset * 2 - 4, cs - 4);
                }

                // Grid lines
                if (cell !== 0 && cell !== 2) {
                    ctx.strokeStyle = this.colours.grid;
                    ctx.lineWidth = 0.5;
                    ctx.strokeRect(px, py, cs, cs);
                }
            }
        }

        // Draw tokens
        if (map.tokens) {
            map.tokens.forEach(token => {
                const px = ox + token.x * cs + cs / 2;
                const py = oy + token.y * cs + cs / 2;
                const radius = cs * 0.35;

                // Token circle
                ctx.beginPath();
                ctx.arc(px, py, radius, 0, Math.PI * 2);
                ctx.fillStyle = token.colour || this.colours.token;
                ctx.fill();
                ctx.strokeStyle = token.borderColour || this.colours.tokenBorder;
                ctx.lineWidth = 2;
                ctx.stroke();

                // Label
                if (token.label) {
                    ctx.fillStyle = this.colours.tokenLabel;
                    ctx.font = `bold ${Math.max(10, cs * 0.3)}px Inter, sans-serif`;
                    ctx.textAlign = 'center';
                    ctx.textBaseline = 'middle';
                    ctx.fillText(token.label, px, py);
                }
            });
        }
    }
}

// =============================================
// TEST MAP — The Blind Crow Tavern
// =============================================
// Cell types: 0=void, 1=floor, 2=wall, 3=door, 4=water
const testMap = {
    name: 'The Blind Crow Tavern',
    width: 20,
    height: 16,
    cells: [
        [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
        [0,2,2,2,2,2,2,2,2,2,0,0,2,2,2,2,2,2,2,0],
        [0,2,1,1,1,1,1,1,1,2,0,0,2,1,1,1,1,1,2,0],
        [0,2,1,1,1,1,1,1,1,2,0,0,2,1,1,1,1,1,2,0],
        [0,2,1,1,1,1,1,1,1,2,2,2,2,1,1,1,1,1,2,0],
        [0,2,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,2,0],
        [0,2,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,2,0],
        [0,2,1,1,1,1,1,1,1,2,2,3,2,1,1,1,1,1,2,0],
        [0,2,2,2,3,2,2,1,1,2,0,0,2,1,1,1,1,1,2,0],
        [0,0,0,0,1,0,0,1,1,2,0,0,2,2,2,3,2,2,2,0],
        [0,0,0,0,1,0,0,1,1,2,0,0,0,0,0,1,0,0,0,0],
        [0,2,2,2,3,2,2,1,1,2,0,0,0,2,2,3,2,2,0,0],
        [0,2,1,1,1,1,2,1,1,2,0,0,0,2,1,1,1,2,0,0],
        [0,2,1,1,1,1,2,2,2,2,0,0,0,2,1,4,1,2,0,0],
        [0,2,1,1,1,1,2,0,0,0,0,0,0,2,2,2,2,2,0,0],
        [0,2,2,2,2,2,2,0,0,0,0,0,0,0,0,0,0,0,0,0]
    ],
    tokens: [
        { x: 5, y: 5, label: 'A', colour: '#2a5a3a', borderColour: '#4a8a5a' },
        { x: 15, y: 3, label: 'B', colour: '#8a3a3a', borderColour: '#aa5a5a' },
        { x: 3, y: 12, label: 'G', colour: '#5a3a8a', borderColour: '#7a5aaa' },
        { x: 15, y: 13, label: '?', colour: '#3a5a8a', borderColour: '#5a7aaa' }
    ]
};

// =============================================
// INITIALISE MAP
// =============================================
const mapCanvas = document.getElementById('map-canvas');
const mapPlaceholder = document.getElementById('map-placeholder');
const mapRenderer = new MapRenderer(mapCanvas);

function initMap() {
    mapPlaceholder.style.display = 'none';
    mapRenderer.resize();
    mapRenderer.loadMap(testMap);
}

// Resize observer to handle container size changes
const mapContainer = document.getElementById('map-container');
const resizeObserver = new ResizeObserver(() => {
    if (mapRenderer.mapData) {
        mapRenderer.resize();
    }
});
resizeObserver.observe(mapContainer);

// Initial map load — slight delay to ensure container has dimensions
setTimeout(initMap, 100);

// =============================================
// POPOUT MAP
// =============================================
let popoutWindow = null;

document.getElementById('popout-map').addEventListener('click', function() {
    // If popout already open, focus it
    if (popoutWindow && !popoutWindow.closed) {
        popoutWindow.focus();
        return;
    }

    // Open new window
    popoutWindow = window.open('', 'StormMap', 'width=800,height=600,resizable=yes');
    if (!popoutWindow) return;

    // Build self-contained HTML with the map renderer and data
    const mapDataJSON = JSON.stringify(mapRenderer.mapData || testMap);

    popoutWindow.document.write(`<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Storm — Map</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: #080810; overflow: hidden; }
        canvas { display: block; width: 100vw; height: 100vh; cursor: grab; }
    </style>
</head>
<body>
    <canvas id="popout-canvas"></canvas>
    <script>
        const canvas = document.getElementById('popout-canvas');
        const ctx = canvas.getContext('2d');
        const mapData = ${mapDataJSON};

        let cellSize = 32;
        let scrollOffsetX = 0;
        let scrollOffsetY = 0;
        let isDragging = false;
        let dragStartX = 0;
        let dragStartY = 0;

        const colours = {
            floor: '#1a1a25', wall: '#2a2a35', wallTop: '#353545',
            grid: 'rgba(42, 90, 58, 0.15)', door: '#bba87e', doorFrame: '#665a3e',
            token: '#2a5a3a', tokenBorder: '#4a8a5a', tokenLabel: '#e8e8e8',
            water: 'rgba(50, 80, 120, 0.4)', background: '#080810'
        };

        function resize() {
            canvas.width = window.innerWidth;
            canvas.height = window.innerHeight;
            fitToWindow();
            render();
        }

        function fitToWindow() {
            const fitW = (canvas.width - 40) / mapData.width;
            const fitH = (canvas.height - 40) / mapData.height;
            cellSize = Math.max(12, Math.min(64, Math.floor(Math.min(fitW, fitH))));
            const mapW = mapData.width * cellSize;
            const mapH = mapData.height * cellSize;
            scrollOffsetX = (canvas.width - mapW) / 2;
            scrollOffsetY = (canvas.height - mapH) / 2;
        }

        function render() {
            const cs = cellSize;
            const ox = scrollOffsetX;
            const oy = scrollOffsetY;

            ctx.fillStyle = colours.background;
            ctx.fillRect(0, 0, canvas.width, canvas.height);

            for (let y = 0; y < mapData.height; y++) {
                for (let x = 0; x < mapData.width; x++) {
                    const cell = mapData.cells[y][x];
                    const px = ox + x * cs;
                    const py = oy + y * cs;
                    if (cell === 0) continue;
                    if (cell === 1 || cell === 3 || cell === 4) {
                        ctx.fillStyle = colours.floor;
                        ctx.fillRect(px, py, cs, cs);
                    }
                    if (cell === 4) {
                        ctx.fillStyle = colours.water;
                        ctx.fillRect(px, py, cs, cs);
                    }
                    if (cell === 2) {
                        ctx.fillStyle = colours.wall;
                        ctx.fillRect(px, py, cs, cs);
                        ctx.fillStyle = colours.wallTop;
                        ctx.fillRect(px, py, cs, cs * 0.3);
                    }
                    if (cell === 3) {
                        const di = cs * 0.3;
                        ctx.fillStyle = colours.doorFrame;
                        ctx.fillRect(px + di, py, cs - di * 2, cs);
                        ctx.fillStyle = colours.door;
                        ctx.fillRect(px + di + 2, py + 2, cs - di * 2 - 4, cs - 4);
                    }
                    if (cell !== 0 && cell !== 2) {
                        ctx.strokeStyle = colours.grid;
                        ctx.lineWidth = 0.5;
                        ctx.strokeRect(px, py, cs, cs);
                    }
                }
            }

            if (mapData.tokens) {
                mapData.tokens.forEach(function(token) {
                    const px = ox + token.x * cs + cs / 2;
                    const py = oy + token.y * cs + cs / 2;
                    const radius = cs * 0.35;
                    ctx.beginPath();
                    ctx.arc(px, py, radius, 0, Math.PI * 2);
                    ctx.fillStyle = token.colour || colours.token;
                    ctx.fill();
                    ctx.strokeStyle = token.borderColour || colours.tokenBorder;
                    ctx.lineWidth = 2;
                    ctx.stroke();
                    if (token.label) {
                        ctx.fillStyle = colours.tokenLabel;
                        ctx.font = 'bold ' + Math.max(10, cs * 0.3) + 'px Inter, sans-serif';
                        ctx.textAlign = 'center';
                        ctx.textBaseline = 'middle';
                        ctx.fillText(token.label, px, py);
                    }
                });
            }
        }

        canvas.addEventListener('mousedown', function(e) {
            isDragging = true;
            dragStartX = e.clientX - scrollOffsetX;
            dragStartY = e.clientY - scrollOffsetY;
            canvas.style.cursor = 'grabbing';
        });

        window.addEventListener('mousemove', function(e) {
            if (!isDragging) return;
            scrollOffsetX = e.clientX - dragStartX;
            scrollOffsetY = e.clientY - dragStartY;
            render();
        });

        window.addEventListener('mouseup', function() {
            isDragging = false;
            canvas.style.cursor = 'grab';
        });

        canvas.addEventListener('wheel', function(e) {
            e.preventDefault();
            var oldSize = cellSize;
            var delta = e.deltaY > 0 ? -2 : 2;
            cellSize = Math.max(16, Math.min(64, cellSize + delta));
            var rect = canvas.getBoundingClientRect();
            var mouseX = e.clientX - rect.left;
            var mouseY = e.clientY - rect.top;
            var scale = cellSize / oldSize;
            scrollOffsetX = mouseX - (mouseX - scrollOffsetX) * scale;
            scrollOffsetY = mouseY - (mouseY - scrollOffsetY) * scale;
            render();
        }, { passive: false });

        window.addEventListener('resize', resize);
        resize();
    <\/script>
</body>
</html>`);
    popoutWindow.document.close();

    // When popout closes, clean up reference
    const checkClosed = setInterval(() => {
        if (popoutWindow && popoutWindow.closed) {
            clearInterval(checkClosed);
            popoutWindow = null;
        }
    }, 500);
});
