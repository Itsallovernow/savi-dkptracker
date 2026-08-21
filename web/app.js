// DKP Auction History - Web Viewer
// API URL configurable at build time
const API_BASE_URL = window.__DKP_API_URL__ || 'https://yk77zxp4qe.execute-api.us-west-1.amazonaws.com/prod';

(function () {
  'use strict';

  // --- State ---
  let allRecords = [];         // All records from API
  let filteredRecords = [];    // Records after client-side filtering + sorting
  let currentPage = 1;
  let autoRefreshInterval = null;
  let isLoading = false;
  let sortColumn = 'timestamp'; // Current sort column
  let sortDirection = 'desc';   // 'asc' or 'desc'

  // --- Expansion Bucket Configuration ---

  /**
   * Expansion bucket definitions (ordered newest-first for display).
   * Each bucket defines a named date range corresponding to an EverQuest expansion era.
   * @type {Array<{ name: string, startDate: string, endDate: string|null }>}
   */
  const EXPANSION_BUCKETS = [
    { name: 'Planes of Power', startDate: '2026-10-01', endDate: null },
    { name: 'Shadows of Luclin', startDate: '2026-01-01', endDate: '2026-09-30' },
    { name: 'Scars of Velious', startDate: '2025-04-01', endDate: '2025-12-31' },
    { name: 'Ruins of Kunark', startDate: '2024-07-01', endDate: '2025-03-31' },
    { name: 'Classic', startDate: '2023-12-01', endDate: '2024-06-30' },
  ];

  /**
   * Classify a single auction record into an expansion bucket.
   * - Records with valid timestamps within a defined range → that bucket's name.
   * - Records with timestamps before Classic's start date → "Classic".
   * - Records with missing, null, or unparseable timestamps → "Other".
   * @param {Object} record - An AuctionRecord with a timestamp field
   * @returns {string} Expansion bucket name
   */
  function classifyRecord(record) {
    if (!record.timestamp) return 'Other';

    const date = new Date(record.timestamp);
    if (isNaN(date.getTime())) return 'Other';

    // Normalize to date-only string (YYYY-MM-DD) for comparison
    const dateStr = date.toISOString().slice(0, 10);

    // Check each bucket for a match
    for (const bucket of EXPANSION_BUCKETS) {
      const afterStart = dateStr >= bucket.startDate;
      const beforeEnd = bucket.endDate === null || dateStr <= bucket.endDate;
      if (afterStart && beforeEnd) {
        return bucket.name;
      }
    }

    // Timestamps before Classic's start date → "Classic"
    const classicStart = EXPANSION_BUCKETS[EXPANSION_BUCKETS.length - 1].startDate;
    if (dateStr < classicStart) {
      return 'Classic';
    }

    // Anything else (shouldn't happen with contiguous ranges, but safety fallback)
    return 'Other';
  }

  /**
   * Group an array of records by expansion bucket.
   * Returns a Map with bucket names as keys and arrays of records as values.
   * The Map preserves insertion order matching EXPANSION_BUCKETS (newest-first).
   * Only includes buckets that contain at least one record, plus "Other" if needed.
   * @param {AuctionRecord[]} records
   * @returns {Map<string, Array>} Bucket name → records
   */
  function groupByExpansion(records) {
    const groups = new Map();

    // Initialize buckets in order (newest-first) so Map iteration order is correct
    for (const bucket of EXPANSION_BUCKETS) {
      groups.set(bucket.name, []);
    }
    groups.set('Other', []);

    // Classify each record
    for (const record of records) {
      const bucketName = classifyRecord(record);
      if (!groups.has(bucketName)) {
        groups.set(bucketName, []);
      }
      groups.get(bucketName).push(record);
    }

    // Remove empty buckets
    for (const [key, value] of groups) {
      if (value.length === 0) {
        groups.delete(key);
      }
    }

    return groups;
  }

  // --- DOM References ---
  const searchInput = document.getElementById('search-input');
  const dateFromInput = document.getElementById('date-from');
  const dateToInput = document.getElementById('date-to');
  const dateClearBtn = document.getElementById('date-clear-btn');
  const refreshBtn = document.getElementById('refresh-btn');
  const autoRefreshCheckbox = document.getElementById('auto-refresh-checkbox');
  const tableBody = document.getElementById('auction-table-body');
  const loadingIndicator = document.getElementById('loading-indicator');
  const errorMessage = document.getElementById('error-message');
  const retryBtn = document.getElementById('retry-btn');
  const paginationPrev = document.getElementById('pagination-prev');
  const paginationNext = document.getElementById('pagination-next');
  const paginationInfo = document.getElementById('pagination-info');
  const noResults = document.getElementById('no-results');
  const statTotalAuctions = document.querySelector('#stat-total-auctions .stat-value');
  const statTotalDkp = document.querySelector('#stat-total-dkp .stat-value');
  const statUniqueWinners = document.querySelector('#stat-unique-winners .stat-value');

  // --- Navigation Bar ---

  /**
   * Render the navigation bar with active-state highlighting.
   * Updates the nav links to visually indicate the currently active view.
   * @param {string} activeView - Currently active view identifier ('auctions' or 'characters')
   */
  function renderNavBar(activeView) {
    const navLinks = document.querySelectorAll('#nav-bar .nav-link');
    navLinks.forEach(link => {
      const linkView = link.dataset.view;
      if (linkView === activeView || (activeView === 'character' && linkView === 'characters')) {
        link.classList.add('nav-link-active');
        link.setAttribute('aria-current', 'page');
      } else {
        link.classList.remove('nav-link-active');
        link.removeAttribute('aria-current');
      }
    });
  }

  // --- API Functions ---

  /**
   * Fetch auction records from the backend API.
   * @param {Object} params - Query parameters
   * @param {string} [params.cursor] - Pagination cursor
   * @param {number} [params.limit] - Number of records to fetch (default 100)
   * @returns {Promise<{records: Array, next_cursor: string|null}>}
   */
  async function fetchAuctions(params = {}) {
    const url = new URL(`${API_BASE_URL}/auctions`);
    const queryParams = { ...params };

    Object.entries(queryParams).forEach(([key, value]) => {
      if (value != null && value !== '') {
        url.searchParams.set(key, value);
      }
    });

    const response = await fetch(url.toString());
    if (!response.ok) {
      throw new Error(`API responded with status ${response.status}`);
    }
    return response.json();
  }

  /**
   * Fetch ALL auction records by following pagination cursors.
   * @returns {Promise<Array>} All records
   */
  async function fetchAllAuctions() {
    let allResults = [];
    let cursor = null;

    do {
      const params = { limit: 100 };
      if (cursor) params.cursor = cursor;
      const data = await fetchAuctions(params);
      const records = data.records || [];
      allResults = allResults.concat(records);
      cursor = data.next_cursor || null;
    } while (cursor);

    return allResults;
  }

  /**
   * Fetch all records via the bulk export endpoint (single request).
   * @returns {Promise<{records: Array, total: number}>}
   */
  async function fetchExport() {
    const url = `${API_BASE_URL}/auctions/export`;
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`Export API responded with status ${response.status}`);
    }
    return response.json();
  }

  /**
   * Fetch aggregate stats from the backend API.
   * @returns {Promise<{total_auctions: number, total_dkp_spent: number, unique_winners: number}>}
   */
  async function fetchStats() {
    const url = `${API_BASE_URL}/auctions/stats`;
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`Stats API responded with status ${response.status}`);
    }
    return response.json();
  }

  // --- Guild Character Data ---

  let guildData = null; // { correlations: { alt → parent }, join_dates: { parent → date } }

  /**
   * Fetch guild character data (correlations + join dates) from the API.
   * Caches the result in memory — only fetches once per page load.
   */
  async function fetchGuildData() {
    if (guildData) return guildData;
    try {
      const url = `${API_BASE_URL}/guild/characters`;
      const response = await fetch(url);
      if (!response.ok) return null;
      guildData = await response.json();
      return guildData;
    } catch (e) {
      console.warn('[Guild] Failed to fetch guild data:', e.message);
      return null;
    }
  }

  /**
   * Get all linked characters for a given character name.
   * Returns { parent, joinDate, linked: [{ name, hasRecords }] } or null.
   * @param {string} characterName
   * @returns {Object|null}
   */
  function getLinkedCharacters(characterName) {
    if (!guildData || !guildData.correlations) return null;

    const correlations = guildData.correlations;
    const joinDates = guildData.join_dates || {};
    const nameLower = characterName.toLowerCase();

    // Find the parent for this character
    let parent = null;

    // Check if this character IS a parent (has entries pointing to it)
    const isParent = Object.values(correlations).some(
      p => p.toLowerCase() === nameLower
    );

    if (isParent) {
      parent = characterName;
    } else {
      // Check if this character is an alt (has a correlation entry)
      for (const [alt, par] of Object.entries(correlations)) {
        if (alt.toLowerCase() === nameLower) {
          parent = par;
          break;
        }
      }
    }

    if (!parent) return null;

    const parentLower = parent.toLowerCase();

    // Find all characters linked to this parent (all alts + the parent itself)
    const linked = [];

    // Add all alts of this parent (exclude current character AND the parent itself)
    for (const [alt, par] of Object.entries(correlations)) {
      if (par.toLowerCase() === parentLower && alt.toLowerCase() !== nameLower && alt.toLowerCase() !== parentLower) {
        const hasRecords = allRecords.some(
          r => r.winner && r.winner.toLowerCase() === alt.toLowerCase()
        );
        linked.push({ name: alt, hasRecords });
      }
    }

    // Add the parent if it's not the current character
    if (parentLower !== nameLower) {
      const hasRecords = allRecords.some(
        r => r.winner && r.winner.toLowerCase() === parentLower
      );
      linked.push({ name: parent, hasRecords });
    }

    // Sort alphabetically
    linked.sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));

    // Get join date
    const joinDate = joinDates[parent] || null;

    return { parent, joinDate, linked };
  }

  // --- Data Loading ---

  const PAGE_SIZE = 50; // Records per page (client-side)
  let isLoadingMore = false; // Background loading flag

  // --- localStorage Cache ---
  const CACHE_KEY = 'dkp_auction_cache';
  const CACHE_TS_KEY = 'dkp_auction_cache_ts'; // newest timestamp in cache

  function saveToCache(records) {
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify(records));
      // Find the newest timestamp to use as "since" for delta fetch
      const newest = records.reduce((max, r) => {
        return (r.timestamp && r.timestamp > max) ? r.timestamp : max;
      }, '');
      if (newest) localStorage.setItem(CACHE_TS_KEY, newest);
    } catch (e) {
      // localStorage full or unavailable — ignore
      console.warn('Could not save to cache:', e);
    }
  }

  function loadFromCache() {
    try {
      const cached = localStorage.getItem(CACHE_KEY);
      if (cached) return JSON.parse(cached);
    } catch (e) {
      // Corrupted cache — ignore
    }
    return null;
  }

  function getCacheTimestamp() {
    return localStorage.getItem(CACHE_TS_KEY) || null;
  }

  function clearCache() {
    localStorage.removeItem(CACHE_KEY);
    localStorage.removeItem(CACHE_TS_KEY);
  }

  /**
   * Load auctions — from cache first (instant), then delta-fetch new records.
   */
  async function loadAuctions() {
    if (isLoading) return;
    isLoading = true;

    showLoading(true);
    hideError();

    // Try loading from cache first for instant display
    const cached = loadFromCache();
    const cachedTimestamp = getCacheTimestamp();

    if (cached && cached.length > 0) {
      allRecords = cached;
      updateStats();
      applyFilter();
      showLoading(false);
      isLoading = false;

      // Re-render active character view if that's where the user is
      if (currentRoute.view === 'characters') {
        renderCharacterListing(allRecords);
      } else if (currentRoute.view === 'character' && currentRoute.param) {
        renderCharacterDetail(currentRoute.param, allRecords);
      }

      // Fetch only records newer than the cache in the background
      fetchDeltaInBackground(cachedTimestamp);
    } else {
      // No cache — full load via bulk export (single request, all records)
      try {
        const exportData = await fetchExport();
        allRecords = exportData.records || [];
        updateStats();
        applyFilter();

        showLoading(false);
        isLoading = false;

        // Re-render active character view if that's where the user is
        if (currentRoute.view === 'characters') {
          renderCharacterListing(allRecords);
        } else if (currentRoute.view === 'character' && currentRoute.param) {
          renderCharacterDetail(currentRoute.param, allRecords);
        }

        // Save full dataset to cache
        saveToCache(allRecords);

        // The export may be stale (served from S3 cache) — fetch delta to catch up
        const newestTs = getCacheTimestamp();
        if (newestTs) {
          fetchDeltaInBackground(newestTs);
        }
      } catch (error) {
        showError();
        console.error('Failed to load auctions:', error);
        showLoading(false);
        isLoading = false;
      }
    }
  }

  /**
   * Fetch records newer than cachedTimestamp and merge into allRecords.
   */
  async function fetchDeltaInBackground(sinceTimestamp) {
    isLoadingMore = true;
    showBackgroundLoading(true);

    try {
      let cursor = null;
      let newRecords = [];

      do {
        const params = {};
        if (sinceTimestamp) params.since = sinceTimestamp;
        if (cursor) params.cursor = cursor;

        const data = await fetchAuctions(params);
        const records = data.records || [];
        newRecords = newRecords.concat(records);
        cursor = data.next_cursor || null;
      } while (cursor);

      if (newRecords.length > 0) {
        // Merge: add new records that aren't already in allRecords (by dedup_key)
        const existingKeys = new Set(allRecords.map(r => r.dedup_key));
        const uniqueNew = newRecords.filter(r => !existingKeys.has(r.dedup_key));

        if (uniqueNew.length > 0) {
          allRecords = allRecords.concat(uniqueNew);
          updateStats();
          applyFilter();
          saveToCache(allRecords);
          showBackgroundLoading(false);
          console.log(`[Cache] Added ${uniqueNew.length} new record(s)`);

          // Re-render active character views so they reflect new records without manual refresh
          if (currentRoute.view === 'characters') {
            renderCharacterListing(allRecords);
          } else if (currentRoute.view === 'character' && currentRoute.param) {
            renderCharacterDetail(currentRoute.param, allRecords);
          }
        } else {
          showBackgroundLoading(false);
        }
      } else {
        showBackgroundLoading(false);
      }
    } catch (error) {
      console.error('Delta fetch error:', error);
      showBackgroundLoading(false);
    } finally {
      isLoadingMore = false;
    }
  }

  /**
   * Load remaining records sequentially (used on first full load).
   * @param {string} cursor - Starting cursor
   */
  async function loadRemainingSequential(cursor) {
    isLoadingMore = true;
    showBackgroundLoading(true);

    try {
      while (cursor) {
        const data = await fetchAuctions({ cursor: cursor });
        const records = data.records || [];
        allRecords = allRecords.concat(records);
        cursor = data.next_cursor || null;

        updateStats();
        applyFilter();
        showBackgroundLoading(true);
      }
    } catch (error) {
      console.error('Background loading error:', error);
    } finally {
      isLoadingMore = false;
      showBackgroundLoading(false);
      // Save complete dataset to cache
      saveToCache(allRecords);
    }
  }

  /**
   * Show/hide the background loading indicator.
   */
  function showBackgroundLoading(show) {
    let indicator = document.getElementById('background-loading');
    if (!indicator && show) {
      indicator = document.createElement('div');
      indicator.id = 'background-loading';
      indicator.className = 'background-loading';
      indicator.setAttribute('role', 'status');
      indicator.innerHTML = `<span class="loading-spinner" aria-hidden="true"></span> Loading all records (${allRecords.length} so far)...`;
      document.querySelector('.pagination').insertAdjacentElement('beforebegin', indicator);
    } else if (indicator && show) {
      indicator.innerHTML = `<span class="loading-spinner" aria-hidden="true"></span> Loading all records (${allRecords.length} so far)...`;
    } else if (indicator && !show) {
      indicator.remove();
    }
  }

  /**
   * Refresh data - clear cache and reload everything.
   */
  function refresh() {
    clearCache();
    allRecords = [];
    isLoading = false;
    loadAuctions();
  }

  // --- Rendering ---

  /**
   * Render a winner name cell as a clickable link to the character detail page.
   * @param {string} winnerName - The winner's character name
   * @returns {string} HTML anchor element string
   */
  function renderWinnerLink(winnerName) {
    if (!winnerName) return '';
    const escaped = escapeHtml(winnerName);
    return `<a href="#character/${encodeURIComponent(winnerName)}" class="winner-link">${escaped}</a>`;
  }

  /**
   * Render auction rows into the table body.
   * @param {Array} records - Array of AuctionRecord objects
   */
  function renderTable(records) {
    tableBody.innerHTML = '';

    if (records.length === 0) {
      noResults.hidden = false;
      return;
    }

    noResults.hidden = true;

    records.forEach((record, index) => {
      // Main auction row
      const row = document.createElement('tr');
      row.className = 'auction-row';
      row.tabIndex = 0;
      row.setAttribute('aria-expanded', 'false');
      row.dataset.auctionId = record.dedup_key || index;

      const dateStr = formatDate(record.timestamp);
      const bidCount = record.bid_count != null ? record.bid_count : (record.bids ? record.bids.length : 0);

      row.innerHTML = `
        <td class="col-item">${escapeHtml(record.item_name)}</td>
        <td class="col-winner">${renderWinnerLink(record.winner)}</td>
        <td class="col-amount">${record.amount}</td>
        <td class="col-date">${escapeHtml(dateStr)}</td>
        <td class="col-bids">${bidCount}</td>
        <td class="col-toggle" aria-hidden="true">&#9660;</td>
      `;

      // Detail row (hidden by default)
      const detailRow = document.createElement('tr');
      detailRow.className = 'bid-detail-row';
      detailRow.hidden = true;

      const detailCell = document.createElement('td');
      detailCell.colSpan = 6;
      // Show loading placeholder — bids will be fetched on expand
      detailCell.innerHTML = '<div class="bid-detail-content"><p>Loading bid history...</p></div>';
      detailRow.appendChild(detailCell);

      let bidsLoaded = false;

      // Click handler for expand/collapse with lazy bid loading
      const handleExpand = async () => {
        const isExpanded = row.getAttribute('aria-expanded') === 'true';
        if (!isExpanded && !bidsLoaded) {
          // Fetch full record detail on first expand
          try {
            if (record.bids && record.bids.length > 0) {
              // Bids already in the record (from cache or paginated fetch)
              detailCell.innerHTML = renderBidHistory(record);
            } else if (record.dedup_key) {
              const detail = await fetchRecordDetail(record.dedup_key);
              record.bids = detail.bids || [];
              record.uploaded_by = detail.uploaded_by || record.uploaded_by;
              record.confirmed_by = detail.confirmed_by || record.confirmed_by;
              detailCell.innerHTML = renderBidHistory(record);
            }
          } catch (e) {
            detailCell.innerHTML = '<div class="bid-detail-content"><p>Failed to load bid history.</p></div>';
          }
          bidsLoaded = true;
        }
        toggleDetail(row, detailRow);
      };

      row.addEventListener('click', (e) => {
        // Don't expand row when clicking the winner link
        if (e.target.closest('.winner-link')) return;
        handleExpand();
      });
      row.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          // Don't expand row when activating the winner link
          if (e.target.closest('.winner-link')) return;
          e.preventDefault();
          handleExpand();
        }
      });

      tableBody.appendChild(row);
      tableBody.appendChild(detailRow);
    });
  }

  /**
   * Fetch full record detail (including bids) by dedup_key.
   */
  async function fetchRecordDetail(dedupKey) {
    const url = `${API_BASE_URL}/auctions/${dedupKey}`;
    const response = await fetch(url);
    if (!response.ok) throw new Error(`Detail fetch failed: ${response.status}`);
    return response.json();
  }

  /**
   * Render bid history HTML for a given auction record.
   * @param {Object} record - AuctionRecord
   * @returns {string} HTML string
   */
  function renderBidHistory(record) {
    if (!record.bids || record.bids.length === 0) {
      return '<div class="bid-detail-content"><p>No bid history available.</p></div>';
    }

    let rows = record.bids.map(bid => `
      <tr>
        <td>${escapeHtml(bid.player)}</td>
        <td>${bid.amount}</td>
        <td>${escapeHtml(bid.bid_type)}</td>
        <td>${bid.is_correction ? 'Yes' : 'No'}</td>
      </tr>
    `).join('');

    // Build uploaded_by / confirmed_by info
    let metaHtml = '';
    const uploadedBy = record.uploaded_by;
    const confirmedBy = record.confirmed_by;

    if (uploadedBy || (confirmedBy && confirmedBy.length > 0)) {
      metaHtml = '<div class="record-meta">';
      if (uploadedBy) {
        metaHtml += `<span class="meta-item">Uploaded by: <strong>${escapeHtml(uploadedBy)}</strong></span>`;
      }
      if (confirmedBy && confirmedBy.length > 0) {
        metaHtml += `<span class="meta-item">Confirmed by: <strong>${confirmedBy.map(n => escapeHtml(n)).join(', ')}</strong></span>`;
      }
      metaHtml += '</div>';
    }

    // Item price history: highest and lowest winning amounts across all auctions for this item
    let priceHistoryHtml = '';
    if (record.item_name) {
      const itemNameLower = record.item_name.toLowerCase();
      const sameItemRecords = allRecords.filter(r =>
        r.item_name && r.item_name.toLowerCase() === itemNameLower && r.amount != null
      );
      if (sameItemRecords.length > 1) {
        const amounts = sameItemRecords.map(r => r.amount);
        const highest = Math.max(...amounts);
        const lowest = Math.min(...amounts);
        priceHistoryHtml = `
          <div class="item-price-history">
            <span class="price-history-label">Item Price History (${sameItemRecords.length} auctions):</span>
            <span class="price-history-stat">Highest: <strong>${highest.toLocaleString()}</strong></span>
            <span class="price-history-stat">Lowest: <strong>${lowest.toLocaleString()}</strong></span>
          </div>`;
      }
    }

    return `
      <div class="bid-detail-content">
        <table class="bid-history-table" aria-label="Bid history for ${escapeHtml(record.item_name)}">
          <thead>
            <tr>
              <th scope="col">Player</th>
              <th scope="col">Amount</th>
              <th scope="col">Type</th>
              <th scope="col">Correction</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
        ${priceHistoryHtml}
        ${metaHtml}
      </div>
    `;
  }

  /**
   * Toggle detail row visibility.
   * @param {HTMLElement} row - Main auction row
   * @param {HTMLElement} detailRow - Detail row to toggle
   */
  function toggleDetail(row, detailRow) {
    const isExpanded = row.getAttribute('aria-expanded') === 'true';
    row.setAttribute('aria-expanded', String(!isExpanded));
    detailRow.hidden = isExpanded;

    // Update toggle indicator
    const toggleCell = row.querySelector('.col-toggle');
    if (toggleCell) {
      toggleCell.innerHTML = isExpanded ? '&#9660;' : '&#9650;';
    }
  }

  // --- Filtering ---

  /**
   * Apply client-side search filter and re-render the table.
   */
  function applyFilter() {
    const query = (searchInput.value || '').trim().toLowerCase();
    const dateFrom = dateFromInput ? dateFromInput.value : '';
    const dateTo = dateToInput ? dateToInput.value : '';

    filteredRecords = allRecords.filter(record => {
      // Text filter
      if (query) {
        const matchesText =
          (record.item_name && record.item_name.toLowerCase().includes(query)) ||
          (record.winner && record.winner.toLowerCase().includes(query)) ||
          (record.bids && record.bids.some(bid =>
            bid.player && bid.player.toLowerCase().includes(query)
          ));
        if (!matchesText) return false;
      }

      // Date range filter
      if (dateFrom || dateTo) {
        if (!record.timestamp) return false;
        // Compare using local date to match what's displayed in the Date column
        const d = new Date(record.timestamp);
        if (isNaN(d.getTime())) return false;
        const localDate = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
        if (dateFrom && localDate < dateFrom) return false;
        if (dateTo && localDate > dateTo) return false;
      }

      return true;
    });

    // Apply current sort
    applySortToFiltered();

    // Reset to page 1 on filter/sort change
    currentPage = 1;
    renderCurrentPage();
  }

  /**
   * Render the current page of filteredRecords.
   */
  function renderCurrentPage() {
    const totalPages = Math.max(1, Math.ceil(filteredRecords.length / PAGE_SIZE));
    if (currentPage > totalPages) currentPage = totalPages;

    const start = (currentPage - 1) * PAGE_SIZE;
    const end = start + PAGE_SIZE;
    const pageRecords = filteredRecords.slice(start, end);

    renderTable(pageRecords);
    updatePaginationControls();
  }

  // --- Sorting ---

  /**
   * Sort filteredRecords in-place based on current sortColumn and sortDirection.
   */
  function applySortToFiltered() {
    const dir = sortDirection === 'asc' ? 1 : -1;

    filteredRecords.sort((a, b) => {
      let valA, valB;

      switch (sortColumn) {
        case 'item_name':
          valA = (a.item_name || '').toLowerCase();
          valB = (b.item_name || '').toLowerCase();
          return dir * valA.localeCompare(valB);
        case 'winner':
          valA = (a.winner || '').toLowerCase();
          valB = (b.winner || '').toLowerCase();
          return dir * valA.localeCompare(valB);
        case 'amount':
          valA = a.amount || 0;
          valB = b.amount || 0;
          return dir * (valA - valB);
        case 'timestamp':
          valA = a.timestamp || '';
          valB = b.timestamp || '';
          return dir * valA.localeCompare(valB);
        case 'bids':
          valA = a.bids ? a.bids.length : 0;
          valB = b.bids ? b.bids.length : 0;
          return dir * (valA - valB);
        default:
          return 0;
      }
    });
  }

  /**
   * Handle column header click for sorting.
   * @param {string} column - Column key to sort by
   */
  function handleSort(column) {
    if (sortColumn === column) {
      // Toggle direction
      sortDirection = sortDirection === 'asc' ? 'desc' : 'asc';
    } else {
      sortColumn = column;
      sortDirection = (column === 'amount' || column === 'bids' || column === 'timestamp') ? 'desc' : 'asc';
    }
    updateSortIndicators();
    applyFilter();
  }

  /**
   * Update the sort arrow indicators in the table header.
   */
  function updateSortIndicators() {
    const headers = document.querySelectorAll('#auction-table thead th[data-sort]');
    headers.forEach(th => {
      const col = th.dataset.sort;
      const arrow = th.querySelector('.sort-arrow');
      if (col === sortColumn) {
        arrow.textContent = sortDirection === 'asc' ? ' \u25B2' : ' \u25BC';
        th.classList.add('sorted');
      } else {
        arrow.textContent = '';
        th.classList.remove('sorted');
      }
    });
  }

  // --- Pagination ---

  /**
   * Update pagination button states and info text.
   */
  function updatePaginationControls() {
    const totalPages = Math.max(1, Math.ceil(filteredRecords.length / PAGE_SIZE));
    paginationPrev.disabled = currentPage <= 1;
    paginationNext.disabled = currentPage >= totalPages;
    paginationInfo.textContent = `Page ${currentPage} of ${totalPages} (${filteredRecords.length} records)`;
  }

  /**
   * Go to the next page of results.
   */
  function goNextPage() {
    const totalPages = Math.ceil(filteredRecords.length / PAGE_SIZE);
    if (currentPage >= totalPages) return;
    currentPage++;
    renderCurrentPage();
  }

  /**
   * Go to the previous page of results.
   */
  function goPrevPage() {
    if (currentPage <= 1) return;
    currentPage--;
    renderCurrentPage();
  }

  // --- Stats ---

  /**
   * Update the stats summary display.
   * Update stats from loaded records (computed client-side for accuracy).
   */
  function updateStats() {
    const totalAuctions = allRecords.length;
    const totalDkp = allRecords.reduce((sum, r) => sum + (r.amount || 0), 0);
    const uniqueWinners = new Set(allRecords.map(r => r.winner).filter(Boolean)).size;

    if (statTotalAuctions) {
      statTotalAuctions.textContent = totalAuctions.toLocaleString();
    }
    if (statTotalDkp) {
      statTotalDkp.textContent = totalDkp.toLocaleString();
    }
    if (statUniqueWinners) {
      statUniqueWinners.textContent = uniqueWinners.toLocaleString();
    }
  }

  // --- UI State Helpers ---

  function showLoading(show) {
    loadingIndicator.hidden = !show;
  }

  function showError() {
    errorMessage.hidden = false;
  }

  function hideError() {
    errorMessage.hidden = true;
  }

  // --- Auto-refresh ---

  /**
   * Start auto-refresh timer (60 seconds).
   */
  function startAutoRefresh() {
    stopAutoRefresh();
    autoRefreshInterval = setInterval(() => {
      // Only fetch new records since last known timestamp — don't full refresh
      const cachedTs = getCacheTimestamp();
      if (cachedTs) {
        fetchDeltaInBackground(cachedTs);
      }
    }, 60000);
  }

  /**
   * Stop auto-refresh timer.
   */
  function stopAutoRefresh() {
    if (autoRefreshInterval) {
      clearInterval(autoRefreshInterval);
      autoRefreshInterval = null;
    }
  }

  // --- Utility Functions ---

  /**
   * Format ISO 8601 timestamp to a readable date string.
   * @param {string} isoString - ISO 8601 timestamp
   * @returns {string} Formatted date
   */
  function formatDate(isoString) {
    if (!isoString) return '--';
    try {
      const date = new Date(isoString);
      if (isNaN(date.getTime())) return isoString;
      return date.toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric'
      });
    } catch {
      return isoString;
    }
  }

  /**
   * Escape HTML special characters to prevent XSS.
   * @param {string} str - String to escape
   * @returns {string} Escaped string
   */
  function escapeHtml(str) {
    if (str == null) return '';
    const div = document.createElement('div');
    div.textContent = String(str);
    return div.innerHTML;
  }

  // --- Router ---

  /** Current route state */
  let currentRoute = { view: 'auctions', param: undefined };

  /** Persistent sort state for character listing (survives re-renders) */
  let characterListSort = { field: 'name', direction: 'asc' };

  /**
   * Parse the current location.hash into a route object.
   * Supported routes:
   *   #auctions       → { view: 'auctions' }
   *   #characters     → { view: 'characters' }
   *   #character/Name → { view: 'character', param: 'Name' }
   * Empty or unrecognized hash defaults to { view: 'auctions' }.
   * @returns {{ view: string, param?: string }}
   */
  function parseRoute() {
    const hash = window.location.hash || '';
    // Remove leading '#'
    const path = hash.startsWith('#') ? hash.slice(1) : hash;

    if (path === 'characters') {
      return { view: 'characters' };
    }

    if (path.startsWith('character/')) {
      const name = path.slice('character/'.length);
      const decoded = decodeURIComponent(name);
      if (decoded) {
        return { view: 'character', param: decoded };
      }
    }

    if (path === 'auctions') {
      return { view: 'auctions' };
    }

    // Default: empty or unrecognized hash → auctions
    return { view: 'auctions' };
  }

  /**
   * Navigate to a given hash route.
   * @param {string} route - e.g., '#character/Playername' or '#auctions'
   */
  function navigateTo(route) {
    window.location.hash = route;
  }

  /**
   * Handle a route change: parse the route and invoke view-switching logic.
   * Hides all view containers, then shows the one matching the current route.
   */
  function handleRouteChange() {
    currentRoute = parseRoute();
    renderNavBar(currentRoute.view);

    // Get view containers
    const auctionView = document.getElementById('auction-view');
    const characterListingView = document.getElementById('character-listing-view');
    const characterDetailView = document.getElementById('character-detail-view');

    // Hide all views
    if (auctionView) auctionView.hidden = true;
    if (characterListingView) characterListingView.hidden = true;
    if (characterDetailView) characterDetailView.hidden = true;

    // Show the view matching the current route
    switch (currentRoute.view) {
      case 'characters':
        if (characterListingView) characterListingView.hidden = false;
        // Render character listing if function is available
        if (typeof renderCharacterListing === 'function') {
          renderCharacterListing(allRecords);
        }
        break;

      case 'character':
        if (characterDetailView) characterDetailView.hidden = false;
        // Render character detail if function is available
        if (typeof renderCharacterDetail === 'function') {
          renderCharacterDetail(currentRoute.param, allRecords);
        }
        break;

      case 'auctions':
      default:
        if (auctionView) auctionView.hidden = false;
        break;
    }

    console.log('[Router] Route changed:', currentRoute);
  }

  /**
   * Initialize the router. Listens for hashchange events.
   * On route change, hides all views and renders the appropriate one.
   */
  function initRouter() {
    window.addEventListener('hashchange', handleRouteChange);
    // Handle the initial route on page load
    handleRouteChange();
  }

  // --- Character Listing Functions ---

  /**
   * Compute unique characters with their total DKP spent.
   * Uses first-seen casing for each winner name (case-preserving).
   * Sorts result alphabetically by name (case-insensitive).
   * @param {AuctionRecord[]} records
   * @returns {Array<{ name: string, totalDkp: number }>} Sorted alphabetically
   */
  function getCharacterSummaries(records) {
    const map = new Map(); // lowercase name → { name (first-seen casing), totalDkp }

    for (const record of records) {
      if (!record.winner) continue;
      const key = record.winner.toLowerCase();
      if (map.has(key)) {
        map.get(key).totalDkp += (record.amount || 0);
      } else {
        map.set(key, { name: record.winner, totalDkp: record.amount || 0 });
      }
    }

    const summaries = Array.from(map.values());
    summaries.sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
    return summaries;
  }

  /**
   * Render the character listing page into the #character-listing-view container.
   * Displays a heading, sort controls, a search input, and a sortable list of characters with DKP totals.
   * Each character name is a clickable link to their detail page.
   * @param {AuctionRecord[]} allRecs - Full record set
   */
  function renderCharacterListing(allRecs) {
    const container = document.getElementById('character-listing-view');
    if (!container) return;

    const summaries = getCharacterSummaries(allRecs);

    function sortSummaries(list) {
      const sorted = [...list];
      if (characterListSort.field === 'name') {
        sorted.sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
      } else {
        sorted.sort((a, b) => a.totalDkp - b.totalDkp);
      }
      if (characterListSort.direction === 'desc') sorted.reverse();
      return sorted;
    }

    function renderList(list) {
      return list.map(s => `
        <li class="character-list-item">
          <a href="#character/${encodeURIComponent(s.name)}" class="character-list-link">${escapeHtml(s.name)}</a>
          <span class="character-list-dkp">${s.totalDkp.toLocaleString()} DKP</span>
        </li>`).join('');
    }

    function getSortArrow(field) {
      if (characterListSort.field !== field) return '';
      return characterListSort.direction === 'asc' ? ' &#9650;' : ' &#9660;';
    }

    function renderSortButtons() {
      return `
        <div class="character-sort-controls">
          <span class="sort-label">Sort by:</span>
          <button type="button" class="character-sort-btn${characterListSort.field === 'name' ? ' active' : ''}" data-sort-field="name" aria-label="Sort by name">
            Name${getSortArrow('name')}
          </button>
          <button type="button" class="character-sort-btn${characterListSort.field === 'dkp' ? ' active' : ''}" data-sort-field="dkp" aria-label="Sort by DKP spent">
            DKP Spent${getSortArrow('dkp')}
          </button>
        </div>`;
    }

    function refresh() {
      const query = container.querySelector('#character-search-input')?.value.trim().toLowerCase() || '';
      const filtered = query
        ? summaries.filter(s => s.name.toLowerCase().includes(query))
        : summaries;
      const sorted = sortSummaries(filtered);

      const listEl = container.querySelector('.character-list');
      if (listEl) listEl.innerHTML = renderList(sorted);

      const sortContainer = container.querySelector('.character-sort-controls');
      if (sortContainer) sortContainer.outerHTML = renderSortButtons();
      wireSort();
    }

    function wireSort() {
      container.querySelectorAll('.character-sort-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          const field = btn.dataset.sortField;
          if (characterListSort.field === field) {
            characterListSort.direction = characterListSort.direction === 'asc' ? 'desc' : 'asc';
          } else {
            characterListSort.field = field;
            characterListSort.direction = field === 'dkp' ? 'desc' : 'asc';
          }
          refresh();
        });
      });
    }

    const sorted = sortSummaries(summaries);

    container.innerHTML = `
      <h2 class="character-listing-heading">Characters</h2>
      <div class="character-listing-controls">
        <div class="character-listing-search-container">
          <label for="character-search-input">Search characters</label>
          <input type="search" id="character-search-input" placeholder="Filter by character name..." autocomplete="off">
        </div>
        ${renderSortButtons()}
      </div>
      <ul class="character-list" role="list">${renderList(sorted)}</ul>`;

    wireSort();

    // Wire up the search/filter input
    const searchEl = document.getElementById('character-search-input');
    if (searchEl) {
      let filterTimeout = null;
      searchEl.addEventListener('input', () => {
        clearTimeout(filterTimeout);
        filterTimeout = setTimeout(() => refresh(), 200);
      });
    }
  }

  // --- Character Detail Functions ---

  /**
   * Filter allRecords for a specific character (case-insensitive winner match).
   * @param {string} characterName - The character name to filter for
   * @returns {AuctionRecord[]} Records where winner matches characterName (case-insensitive)
   */
  function filterRecordsForCharacter(characterName) {
    if (!characterName) return [];
    const nameLower = characterName.toLowerCase();
    return allRecords.filter(record =>
      record.winner && record.winner.toLowerCase() === nameLower
    );
  }

  /**
   * Render the DKP summary table showing expansion name, total DKP, and item count per bucket.
   * Includes a grand total row across all buckets.
   * @param {Map<string, AuctionRecord[]>} groupedRecords - Map from groupByExpansion()
   * @returns {string} HTML string for the DKP summary table
   */
  function renderDkpSummary(groupedRecords) {
    let grandTotalDkp = 0;
    let grandTotalItems = 0;

    let rows = '';
    for (const [bucketName, records] of groupedRecords) {
      const totalDkp = records.reduce((sum, r) => sum + (r.amount || 0), 0);
      const itemCount = records.length;
      grandTotalDkp += totalDkp;
      grandTotalItems += itemCount;

      rows += `
        <tr>
          <td>${escapeHtml(bucketName)}</td>
          <td>${itemCount}</td>
          <td>${totalDkp.toLocaleString()}</td>
        </tr>`;
    }

    // Grand total footer row
    rows += `
      <tr class="dkp-summary-total">
        <td><strong>Grand Total</strong></td>
        <td><strong>${grandTotalItems}</strong></td>
        <td><strong>${grandTotalDkp.toLocaleString()}</strong></td>
      </tr>`;

    return `
      <table class="dkp-summary-table" aria-label="DKP spending summary by expansion">
        <thead>
          <tr>
            <th scope="col">Expansion</th>
            <th scope="col">Items Won</th>
            <th scope="col">Total DKP</th>
          </tr>
        </thead>
        <tbody>${rows}
        </tbody>
      </table>`;
  }

  /**
   * Render a "no records found" message for a character, with a link back to the character listing.
   * @param {string} characterName - The character name that was searched
   * @returns {string} HTML string
   */
  function renderNoRecordsMessage(characterName) {
    return `
      <div class="no-records-message" role="status">
        <p>No records found for ${escapeHtml(characterName)}</p>
        <a href="#characters" class="back-link">← Back to character listing</a>
      </div>`;
  }

  /**
   * Render the item breakout section for a single expansion bucket.
   * Items are sorted by timestamp descending (newest-first).
   * Missing timestamps display "--" as date; missing amounts are treated as 0.
   * @param {string} bucketName - The expansion bucket name
   * @param {AuctionRecord[]} records - Array of records for this bucket
   * @returns {string} HTML string for the item breakout section
   */
  function renderItemBreakout(bucketName, records) {
    // Sort records by timestamp descending (newest-first)
    const sorted = [...records].sort((a, b) => {
      const tsA = a.timestamp || '';
      const tsB = b.timestamp || '';
      // Descending: b before a
      if (tsB > tsA) return 1;
      if (tsB < tsA) return -1;
      return 0;
    });

    let rows = '';
    for (const record of sorted) {
      const itemName = escapeHtml(record.item_name || '');
      const amount = record.amount != null ? record.amount : 0;
      const dateStr = formatDate(record.timestamp);

      rows += `
        <tr>
          <td>${itemName}</td>
          <td>${amount}</td>
          <td>${escapeHtml(dateStr)}</td>
        </tr>`;
    }

    return `
      <div class="item-breakout-section">
        <h4>${escapeHtml(bucketName)}</h4>
        <table class="item-breakout-table" aria-label="Items won in ${escapeHtml(bucketName)}">
          <thead>
            <tr>
              <th scope="col">Item</th>
              <th scope="col">DKP</th>
              <th scope="col">Date</th>
            </tr>
          </thead>
          <tbody>${rows}
          </tbody>
        </table>
      </div>`;
  }

  /**
   * Render the full character detail page into the #character-detail-view container.
   * Composes a back link, character name heading, DKP summary table, and item breakout sections.
   * Only displays buckets that contain at least one record, ordered newest to oldest.
   * @param {string} characterName - The character to display
   * @param {AuctionRecord[]} allRecs - Full record set
   */
  function renderCharacterDetail(characterName, allRecs) {
    const container = document.getElementById('character-detail-view');
    if (!container) return;

    // 1. Filter records for this character (case-insensitive)
    const characterRecords = filterRecordsForCharacter(characterName);

    // 2. If no records found, show a "no records" message and return
    if (characterRecords.length === 0) {
      container.innerHTML = renderNoRecordsMessage(characterName);
      // Still try to show linked characters even if no DKP records
      renderLinkedCharactersSection(container, characterName);
      return;
    }

    // 3. Group records by expansion bucket (Map preserves newest-first order, excludes empty buckets)
    const groupedRecords = groupByExpansion(characterRecords);

    // 4. Build the back link
    const backLink = `<a href="#characters" class="back-link">&larr; Back to characters</a>`;

    // 5. Build the character name heading
    const heading = `<h2 class="character-detail-heading">${escapeHtml(characterName)}</h2>`;

    // 6. Build DKP summary table
    const summaryHtml = renderDkpSummary(groupedRecords);

    // 7. Build item breakout for each bucket (Map iteration order = newest-first)
    let breakoutsHtml = '';
    for (const [bucketName, records] of groupedRecords) {
      breakoutsHtml += renderItemBreakout(bucketName, records);
    }

    // 8. Compose and set innerHTML
    container.innerHTML = `
      <div class="character-detail-container">
        ${backLink}
        ${heading}
        <div id="linked-characters-slot"></div>
        <section class="dkp-summary-section" aria-label="DKP Summary">
          <h3>DKP Summary</h3>
          ${summaryHtml}
        </section>
        <section class="item-breakouts-section" aria-label="Item Breakouts">
          <h3>Items Won by Expansion</h3>
          ${breakoutsHtml}
        </section>
      </div>`;

    // 9. Render linked characters (async — fills in the slot when data arrives)
    renderLinkedCharactersSection(container, characterName);
  }

  /**
   * Render linked characters info into the character detail page.
   * Fetches guild data if not already loaded, then populates the slot.
   * @param {HTMLElement} container - The character detail view container
   * @param {string} characterName - Current character name
   */
  async function renderLinkedCharactersSection(container, characterName) {
    await fetchGuildData();
    const info = getLinkedCharacters(characterName);
    if (!info || info.linked.length === 0) return;

    const slot = container.querySelector('#linked-characters-slot');
    // If no slot (e.g. no records view), append to container
    const target = slot || container;

    let linkedHtml = info.linked.map(c => {
      if (c.hasRecords) {
        return `<a href="#character/${encodeURIComponent(c.name)}" class="linked-char-link">${escapeHtml(c.name)}</a>`;
      }
      return `<span class="linked-char-nolink">${escapeHtml(c.name)}</span>`;
    }).join(', ');

    let joinDateHtml = '';
    if (info.joinDate) {
      const d = new Date(info.joinDate);
      const formatted = isNaN(d.getTime()) ? info.joinDate : d.toLocaleDateString();
      joinDateHtml = `<div class="linked-join-date">Member since: <strong>${formatted}</strong></div>`;
    }

    const html = `
      <div class="linked-characters-section">
        <div class="linked-characters-info">
          <span class="linked-label">Linked characters:</span> ${linkedHtml}
        </div>
        ${joinDateHtml}
      </div>`;

    if (slot) {
      slot.innerHTML = html;
    } else {
      target.insertAdjacentHTML('beforeend', html);
    }
  }

  // --- Event Listeners ---

  // Sortable column headers
  document.querySelectorAll('#auction-table thead th[data-sort]').forEach(th => {
    th.addEventListener('click', () => handleSort(th.dataset.sort));
    th.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        handleSort(th.dataset.sort);
      }
    });
  });

  // Search input - filter on each keystroke (debounced)
  let searchTimeout = null;
  searchInput.addEventListener('input', () => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => {
      applyFilter();
    }, 200);
  });

  // Date range filter
  if (dateFromInput) dateFromInput.addEventListener('change', () => applyFilter());
  if (dateToInput) dateToInput.addEventListener('change', () => applyFilter());
  if (dateClearBtn) {
    dateClearBtn.addEventListener('click', () => {
      if (dateFromInput) dateFromInput.value = '';
      if (dateToInput) dateToInput.value = '';
      applyFilter();
    });
  }

  // Manual refresh
  refreshBtn.addEventListener('click', () => {
    refresh();
  });

  // Auto-refresh toggle
  autoRefreshCheckbox.addEventListener('change', () => {
    if (autoRefreshCheckbox.checked) {
      startAutoRefresh();
    } else {
      stopAutoRefresh();
    }
  });

  // Retry button in error banner
  retryBtn.addEventListener('click', () => {
    refresh();
  });

  // Pagination buttons
  paginationNext.addEventListener('click', () => {
    goNextPage();
  });

  paginationPrev.addEventListener('click', () => {
    goPrevPage();
  });

  // --- Initialization ---

  // Initialize the router (handles current hash and listens for changes)
  initRouter();

  // Load data on page load
  loadAuctions();

})();
