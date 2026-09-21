
(() => {
    'use strict';

    const CSS_PATH = 'AI/chessmind/portfolio-ai.css';
    const AI_URL = 'AI/chessmind/web/index.html';

    function addStylesheet() {
        if (document.querySelector(`link[data-chessmind-css="${CSS_PATH}"]`)) return;
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = CSS_PATH;
        link.dataset.chessmindCss = CSS_PATH;
        document.head.appendChild(link);
    }

    function createAiNode() {
        const flow = document.querySelector('.system-flow');
        if (!flow) return null;

        let node = flow.querySelector('.system-node-ai');
        if (node) return node;

        const hardware = flow.querySelector('[data-system="hardware"]');
        if (!hardware) return null;

        const arrow = document.createElement('span');
        arrow.className = 'system-line';
        arrow.textContent = '→';

        node = document.createElement('button');
        node.type = 'button';
        node.className = 'system-node system-node-ai';
        node.dataset.system = 'ai';
        node.setAttribute('aria-label', 'AI — click to experience ChessMind AI');
        node.innerHTML = `
            <span class="system-dot"></span>
            <span>AI</span>
            <span class="ai-play-hint" aria-hidden="true">
                <span class="ai-play-hint-dot"></span>
                <span>CLICK TO EXPERIENCE AI</span>
                <span class="ai-play-hint-arrow">↗</span>
            </span>
        `;

        hardware.insertAdjacentElement('afterend', arrow);
        arrow.insertAdjacentElement('afterend', node);
        return node;
    }

    function createModal() {
        let modal = document.getElementById('chessmindModal');
        if (modal) return modal;

        modal = document.createElement('div');
        modal.className = 'chessmind-modal';
        modal.id = 'chessmindModal';
        modal.setAttribute('aria-hidden', 'true');
        modal.innerHTML = `
            <div class="chessmind-dialog" role="dialog" aria-modal="true" aria-labelledby="chessmindTitle">
                <div class="chessmind-toolbar">
                    <div class="chessmind-title-wrap">
                        <span class="chessmind-status" aria-hidden="true"></span>
                        <div>
                            <div class="chessmind-title" id="chessmindTitle">CHESSMIND · AI INTELLIGENCE</div>
                            <div class="chessmind-subtitle">Interactive chess analysis powered by the supplied ChessMind Core</div>
                        </div>
                    </div>
                    <button class="chessmind-close" id="chessmindClose" type="button" aria-label="Close ChessMind AI">×</button>
                </div>
                <div class="chessmind-frame-wrap">
                    <iframe class="chessmind-frame" id="chessmindFrame" title="ChessMind AI" allow="fullscreen" allowfullscreen loading="lazy"></iframe>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
        return modal;
    }

    function init() {
        addStylesheet();
        const aiNode = createAiNode();
        const modal = createModal();
        const frame = document.getElementById('chessmindFrame');
        const close = document.getElementById('chessmindClose');
        if (!aiNode || !modal || !frame || !close) return;

        let lastFocused = null;

        const getUrl = () => window.location.protocol === 'file:'
            ? 'http://127.0.0.1:8080/AI/chessmind/web/index.html'
            : new URL(AI_URL, document.baseURI).href;

        function open() {
            lastFocused = document.activeElement;
            const url = getUrl();
            if (frame.getAttribute('src') !== url) frame.setAttribute('src', url);
            modal.classList.add('open');
            modal.setAttribute('aria-hidden', 'false');
            document.body.classList.add('chessmind-open');
            window.chessMindOpen = true;
            window.setTimeout(() => close.focus(), 0);
        }

        function closeModal() {
            modal.classList.remove('open');
            modal.setAttribute('aria-hidden', 'true');
            document.body.classList.remove('chessmind-open');
            window.chessMindOpen = false;
            frame.src = 'about:blank';
            if (lastFocused && typeof lastFocused.focus === 'function') lastFocused.focus();
        }

        aiNode.addEventListener('click', () => {
            document.querySelectorAll('.system-node').forEach(item => item.classList.remove('active'));
            open();
        });
        close.addEventListener('click', closeModal);
        modal.addEventListener('click', event => {
            if (event.target === modal) closeModal();
        });
        document.addEventListener('keydown', event => {
            if (event.key === 'Escape' && modal.classList.contains('open')) closeModal();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }
})();
