// PDF2BOOK Dashboard Page — Alpine.js component
// Handles: stats / books / library / inbox loading + number animations

function dashboardPage() {
  return {
    stats: null,
    books: [],
    libraryBooks: [],
    inboxFiles: [],
    loading: true,
    animatedValues: { workspace: 0, library: 0, inbox: 0 },

    async init() {
      await this.loadAll();
      this.loading = false;
      this.animateNumbers();
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
