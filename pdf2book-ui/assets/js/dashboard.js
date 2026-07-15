// PDF2BOOK Dashboard Page — Alpine.js component
// Handles: stats / books / library / inbox loading + number animations
//          + resizable metric cards (1x1 → 2x2, with smooth transitions)

function dashboardPage() {
  return {
    stats: null,
    books: [],
    libraryBooks: [],
    inboxFiles: [],
    loading: true,
    animatedValues: { workspace: 0, library: 0, inbox: 0 },

    // Resizable card layout: each card has {w, h} where w=cols, h=rows
    // Valid sizes: 1x1, 1x2, 1x3, 2x1, 3x1, 2x2
    cardSizes: [
      { w: 1, h: 1 },
      { w: 1, h: 1 },
      { w: 1, h: 1 },
    ],
    resizeMenuOpen: -1,  // which card's resize menu is open (-1 = none)
    resizingCards: [],   // cards currently playing resize animation
    sizeOptions: [
      { w: 1, h: 1, label: '1 × 1' },
      { w: 2, h: 1, label: '2 × 1' },
      { w: 3, h: 1, label: '3 × 1' },
      { w: 1, h: 2, label: '1 × 2' },
      { w: 2, h: 2, label: '2 × 2' },
      { w: 1, h: 3, label: '1 × 3' },
    ],

    async init() {
      this.loadLayout();
      await this.loadAll();
      this.loading = false;
      this.animateNumbers();
      // Close resize menu on outside click
      document.addEventListener('click', (e) => {
        if (!e.target.closest('.resize-menu-wrap')) {
          this.resizeMenuOpen = -1;
        }
      });
    },

    // --- Layout persistence ---
    loadLayout() {
      try {
        const saved = localStorage.getItem('pdf2book_dashboard_layout');
        if (saved) {
          const parsed = JSON.parse(saved);
          if (Array.isArray(parsed) && parsed.length === 3) {
            this.cardSizes = parsed.map(s => ({
              w: Math.min(Math.max(s.w || 1, 1), 3),
              h: Math.min(Math.max(s.h || 1, 1), 3),
            }));
          }
        }
      } catch (e) { /* ignore corrupt storage */ }
    },

    saveLayout() {
      try {
        localStorage.setItem('pdf2book_dashboard_layout', JSON.stringify(this.cardSizes));
      } catch (e) { /* ignore */ }
    },

    // --- Card sizing ---
    setCardSize(index, w, h) {
      if (index < 0 || index >= this.cardSizes.length) return;
      const cur = this.cardSizes[index];
      if (cur.w === w && cur.h === h) { this.resizeMenuOpen = -1; return; }
      this.cardSizes[index] = { w, h };
      this.resizeMenuOpen = -1;
      this.saveLayout();
      // Trigger resize pulse animation
      this.resizingCards = [...this.resizingCards, index];
      setTimeout(() => {
        this.resizingCards = this.resizingCards.filter(i => i !== index);
      }, 450);
    },

    cardStyle(index) {
      const s = this.cardSizes[index];
      if (!s) return '';
      return `grid-column: span ${s.w}; grid-row: span ${s.h};`;
    },

    isCardSize(index, w, h) {
      const s = this.cardSizes[index];
      return s && s.w === w && s.h === h;
    },

    toggleResizeMenu(index) {
      this.resizeMenuOpen = this.resizeMenuOpen === index ? -1 : index;
    },

    // --- Data loading ---
    async loadAll() {
      const [statsRes, booksRes, libraryRes, inboxRes] = await Promise.allSettled([
        fetch('/api/stats').then(r => r.json()),
        fetch('/api/books').then(r => r.json()),
        fetch('/api/library').then(r => r.json()),
        fetch('/api/inbox').then(r => r.json()),
      ]);
      if (statsRes.status === 'fulfilled') this.stats = statsRes.value;
      if (booksRes.status === 'fulfilled') this.books = booksRes.value.books || [];
      if (libraryRes.status === 'fulfilled') this.libraryBooks = libraryRes.value.books || [];
      if (inboxRes.status === 'fulfilled') this.inboxFiles = inboxRes.value.files || [];
    },

    animateNumbers() {
      const targets = {
        workspace: this.stats?.workspace_count || 0,
        library: this.stats?.library_count || 0,
        inbox: this.stats?.inbox_count || 0,
      };
      const duration = 800;
      const start = performance.now();
      const tick = (now) => {
        const elapsed = now - start;
        const t = Math.min(elapsed / duration, 1);
        // easeOutCubic for a smooth, professional deceleration
        const eased = 1 - Math.pow(1 - t, 3);
        this.animatedValues.workspace = Math.round(targets.workspace * eased);
        this.animatedValues.library = Math.round(targets.library * eased);
        this.animatedValues.inbox = Math.round(targets.inbox * eased);
        if (t < 1) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    },

    formatSize(bytes) {
      const n = Number(bytes) || 0;
      if (n < 1024) return n + ' B';
      if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
      if (n < 1024 * 1024 * 1024) return (n / (1024 * 1024)).toFixed(1) + ' MB';
      return (n / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
    },

    formatDate(iso) {
      if (!iso) return '—';
      const d = new Date(iso);
      if (isNaN(d.getTime())) return '—';
      const pad = (x) => String(x).padStart(2, '0');
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    },

    get recentBooks() {
      // Build a lookup of library modified_at by stem (for sorting)
      const libTime = new Map();
      for (const b of this.libraryBooks) libTime.set(b.stem, b.modified_at);
      return [...this.books]
        .map(b => ({ ...b, _ts: libTime.get(b.stem) || '' }))
        .sort((a, b) => (b._ts || '').localeCompare(a._ts || ''))
        .slice(0, 5);
    },

    async refresh() {
      this.loading = true;
      await this.loadAll();
      this.loading = false;
      this.animateNumbers();
    },
  };
}
