// ==UserScript==
// @name         Open WebUI Model Enhancer
// @namespace    http://tampermonkey.net/
// @version      1.0
// @description  Enhanced model selection with persistence, search, and favorites
// @match        http://localhost:8080/*
// @match        http://127.0.0.1:8080/*
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_registerMenuCommand
// @run-at       document-end
// ==/UserScript==

(function() {
    'use strict';

    // Storage keys
    const STORAGE_KEYS = {
        LAST_MODEL: 'daemon_last_selected_model',
        FAVORITES: 'daemon_favorite_models',
        SEARCH_ENABLED: 'daemon_search_enabled',
        TIER1_INDICATOR: 'daemon_tier1_indicator'
    };

    // Tier-1 models (benchmark tier)
    const TIER1_MODELS = [
        'openrouter/claude-opus',
        'openrouter/kimi-k2.5',
        'openrouter/kimi-k2.5'
    ];

    // State
    let modelObserver = null;
    let searchBox = null;
    let initialized = false;

    // Get stored value with fallback
    function getStored(key, defaultValue = null) {
        try {
            const value = localStorage.getItem(key);
            return value !== null ? JSON.parse(value) : defaultValue;
        } catch (e) {
            return defaultValue;
        }
    }

    // Set stored value
    function setStored(key, value) {
        try {
            localStorage.setItem(key, JSON.stringify(value));
        } catch (e) {
            console.error('Failed to store value:', e);
        }
    }

    // Check if model is Tier-1
    function isTier1(modelId) {
        return TIER1_MODELS.some(tier => modelId.includes(tier) || tier.includes(modelId));
    }

    // Check if model is favorite
    function isFavorite(modelId) {
        const favorites = getStored(STORAGE_KEYS.FAVORITES, []);
        return favorites.includes(modelId);
    }

    // Toggle favorite status
    function toggleFavorite(modelId) {
        const favorites = getStored(STORAGE_KEYS.FAVORITES, []);
        const index = favorites.indexOf(modelId);

        if (index > -1) {
            favorites.splice(index, 1);
        } else {
            favorites.push(modelId);
        }

        setStored(STORAGE_KEYS.FAVORITES, favorites);
        return index === -1; // Returns true if added, false if removed
    }

    // Get model display info
    function getModelInfo(modelElement) {
        const modelId = modelElement.getAttribute('data-value') ||
                       modelElement.textContent?.trim();

        if (!modelId) return null;

        return {
            id: modelId,
            isTier1: isTier1(modelId),
            isFavorite: isFavorite(modelId),
            capabilities: modelElement.getAttribute('data-capabilities') || 'chat, streaming'
        };
    }

    // Create search box
    function createSearchBox() {
        const container = document.createElement('div');
        container.className = 'daemon-model-search';
        container.style.cssText = `
            padding: 8px 12px;
            border-bottom: 1px solid rgba(128, 128, 128, 0.2);
            background: inherit;
        `;

        const input = document.createElement('input');
        input.type = 'text';
        input.placeholder = '🔍 Search models by name or capability...';
        input.style.cssText = `
            width: 100%;
            padding: 8px 12px;
            border: 1px solid rgba(128, 128, 128, 0.3);
            border-radius: 6px;
            background: rgba(128, 128, 128, 0.1);
            color: inherit;
            font-size: 14px;
            outline: none;
        `;

        input.addEventListener('input', (e) => {
            filterModels(e.target.value);
        });

        input.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                input.value = '';
                filterModels('');
                input.blur();
            }
        });

        container.appendChild(input);
        searchBox = input;
        return container;
    }

    // Create favorites filter toggle
    function createFavoritesToggle() {
        const container = document.createElement('div');
        container.className = 'daemon-favorites-toggle';
        container.style.cssText = `
            padding: 4px 12px;
            border-bottom: 1px solid rgba(128, 128, 128, 0.2);
            display: flex;
            gap: 8px;
            font-size: 12px;
        `;

        const favoritesBtn = document.createElement('button');
        favoritesBtn.textContent = '⭐ Favorites';
        favoritesBtn.style.cssText = `
            padding: 4px 8px;
            border: 1px solid rgba(128, 128, 128, 0.3);
            border-radius: 4px;
            background: transparent;
            color: inherit;
            cursor: pointer;
            font-size: 12px;
        `;
        favoritesBtn.onclick = () => filterModels('favorites');

        const tier1Btn = document.createElement('button');
        tier1Btn.textContent = '👑 Tier-1';
        tier1Btn.style.cssText = favoritesBtn.style.cssText;
        tier1Btn.onclick = () => filterModels('tier1');

        const allBtn = document.createElement('button');
        allBtn.textContent = 'All';
        allBtn.style.cssText = favoritesBtn.style.cssText + ' background: rgba(128, 128, 128, 0.2);';
        allBtn.onclick = () => {
            if (searchBox) searchBox.value = '';
            filterModels('');
        };

        container.appendChild(allBtn);
        container.appendChild(tier1Btn);
        container.appendChild(favoritesBtn);

        return container;
    }

    // Filter models based on search term
    function filterModels(searchTerm) {
        const modelItems = document.querySelectorAll('[role="option"], .model-item, [data-value*="openrouter"]');
        const favorites = getStored(STORAGE_KEYS.FAVORITES, []);

        modelItems.forEach(item => {
            const modelId = item.getAttribute('data-value') || item.textContent?.trim() || '';
            const isFav = favorites.includes(modelId);
            const isT1 = isTier1(modelId);

            let shouldShow = true;

            if (searchTerm === 'favorites') {
                shouldShow = isFav;
            } else if (searchTerm === 'tier1') {
                shouldShow = isT1;
            } else if (searchTerm) {
                const lowerSearch = searchTerm.toLowerCase();
                const lowerModel = modelId.toLowerCase();
                shouldShow = lowerModel.includes(lowerSearch);
            }

            item.style.display = shouldShow ? '' : 'none';

            // Add visual indicators
            if (shouldShow && !item.querySelector('.daemon-indicators')) {
                addModelIndicators(item, { isFavorite: isFav, isTier1: isT1 });
            }
        });
    }

    // Add visual indicators to model item
    function addModelIndicators(item, info) {
        const existing = item.querySelector('.daemon-indicators');
        if (existing) existing.remove();

        const indicators = document.createElement('span');
        indicators.className = 'daemon-indicators';
        indicators.style.cssText = `
            margin-left: 8px;
            font-size: 12px;
            opacity: 0.8;
        `;

        let indicatorText = '';
        if (info.isTier1) indicatorText += '👑 ';
        if (info.isFavorite) indicatorText += '⭐ ';

        if (indicatorText) {
            indicators.textContent = indicatorText;

            // Add tooltip
            indicators.title = [
                info.isTier1 ? 'Tier-1 (Premium Performance)' : '',
                info.isFavorite ? 'Favorite' : ''
            ].filter(Boolean).join(' | ');

            // Try to append to text content or find suitable element
            const textElement = item.querySelector('span, .text, [class*="text"]') || item;
            if (textElement && textElement !== item) {
                textElement.appendChild(indicators);
            } else {
                item.appendChild(indicators);
            }
        }
    }

    // Save last selected model
    function saveLastModel(modelId) {
        if (modelId) {
            setStored(STORAGE_KEYS.LAST_MODEL, modelId);
            console.log('[Daemon] Saved last model:', modelId);
        }
    }

    // Restore last model
    function restoreLastModel() {
        const lastModel = getStored(STORAGE_KEYS.LAST_MODEL);
        if (!lastModel) return;

        // Find and click the model option
        const modelItems = document.querySelectorAll('[role="option"], .model-item, [data-value]');
        modelItems.forEach(item => {
            const itemValue = item.getAttribute('data-value') || item.textContent?.trim();
            if (itemValue && (itemValue === lastModel || itemValue.includes(lastModel))) {
                console.log('[Daemon] Restoring last model:', lastModel);
                item.click();
            }
        });
    }

    // Intercept model selection
    function interceptModelSelection() {
        document.addEventListener('click', (e) => {
            const target = e.target.closest('[role="option"], .model-item, [data-value*="openrouter"]');
            if (target) {
                const modelId = target.getAttribute('data-value') || target.textContent?.trim();
                if (modelId && modelId.includes('openrouter')) {
                    saveLastModel(modelId);
                }
            }
        }, true);
    }

    // Add right-click context menu for favorites
    function addContextMenu() {
        document.addEventListener('contextmenu', (e) => {
            const target = e.target.closest('[role="option"], .model-item, [data-value*="openrouter"]');
            if (!target) return;

            const modelId = target.getAttribute('data-value') || target.textContent?.trim();
            if (!modelId || !modelId.includes('openrouter')) return;

            e.preventDefault();

            const isFav = isFavorite(modelId);
            const action = isFav ? 'Remove from favorites' : 'Add to favorites';

            if (confirm(`${action}: ${modelId.replace('openrouter/', '')}?`)) {
                toggleFavorite(modelId);
                filterModels(searchBox?.value || '');
            }
        });
    }

    // Enhance model dropdown
    function enhanceModelDropdown() {
        // Find model dropdown or selector
        const dropdowns = document.querySelectorAll('[class*="model"], [class*="dropdown"], select');

        dropdowns.forEach(dropdown => {
            if (dropdown.classList.contains('daemon-enhanced')) return;

            const parent = dropdown.parentElement;
            if (!parent) return;

            // Add search box if not present
            if (!parent.querySelector('.daemon-model-search')) {
                const searchContainer = createSearchBox();
                const favoritesToggle = createFavoritesToggle();

                parent.insertBefore(searchContainer, dropdown);
                parent.insertBefore(favoritesToggle, dropdown);

                dropdown.classList.add('daemon-enhanced');
            }
        });
    }

    // Main initialization
    function init() {
        if (initialized) return;
        initialized = true;

        console.log('[Daemon Model Enhancer] Initializing...');

        // Intercept model selections
        interceptModelSelection();

        // Add context menu
        addContextMenu();

        // Enhance UI when model dropdown appears
        modelObserver = new MutationObserver((mutations) => {
            mutations.forEach((mutation) => {
                if (mutation.type === 'childList') {
                    mutation.addedNodes.forEach((node) => {
                        if (node.nodeType === 1) { // Element node
                            // Check if it's a model dropdown or contains one
                            if (node.matches?.('[class*="model"], [class*="dropdown"]') ||
                                node.querySelector?.('[class*="model"], [class*="dropdown"]')) {
                                setTimeout(() => {
                                    enhanceModelDropdown();
                                    restoreLastModel();
                                }, 100);
                            }
                        }
                    });
                }
            });
        });

        // Observe body for dynamically added dropdowns
        modelObserver.observe(document.body, {
            childList: true,
            subtree: true
        });

        // Initial enhancement
        enhanceModelDropdown();

        // Try to restore last model after a short delay
        setTimeout(restoreLastModel, 500);

        console.log('[Daemon Model Enhancer] Initialized');
    }

    // Wait for page to be ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Re-init on navigation (for SPAs)
    let lastUrl = location.href;
    new MutationObserver(() => {
        const url = location.href;
        if (url !== lastUrl) {
            lastUrl = url;
            initialized = false;
            setTimeout(init, 500);
        }
    }).observe(document, { subtree: true, childList: true });

})();