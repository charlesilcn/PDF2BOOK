// PDF2BOOK Library Page — Alpine.js component
// Handles: EPUB list loading, search filter, view switching, download/delete/edit actions

function libraryPage() {
  return {
    books: [],
    workspaceBooks: [],
    searchQuery: '',
    viewMode: 'grid',
    loading: true,
    totalSizeBytes: 0,

    async init() {
      await Promise.all([this.loadLibrary(), this.loadBooks()]);
      this.loading = false;
    },

    async loadLibrary() {
      try {
        const resp = await fetch('/api/library');
        const data = await resp.json();
        this.books = data.books || [];
        this.totalSizeBytes = data.total_size_bytes || 0;
      } catch (e) {
        console.error('Failed to load library:', e);
        this.books = [];
        this.totalSizeBytes = 0;
      }
    },

    async loadBooks() {
      try {
        const resp = await fetch('/api/books');
        const data = await resp.json();
        this.workspaceBooks = (data.books || []).filter(b => b.has_book_md);
      } catch (e) {
        console.error('Failed to load workspace books:', e);
        this.workspaceBooks = [];
      }
    },

    get filteredBooks() {
      const q = this.searchQuery.trim().toLowerCase();
      if (!q) return this.books;
      return this.books.filter(b => (b.stem || '').toLowerCase().includes(q));
    },

    get totalSize() {
      return this.formatSize(this.totalSizeBytes);
    },

    formatSize(bytes) {
      if (!bytes || bytes <= 0) return '0 B';
      const units = ['B', 'KB', 'MB', 'GB'];
      let val = bytes;
      let idx = 0;
      while (val >= 1024 && idx < units.length - 1) {
        val /= 1024;
        idx++;
      }
      const rounded = val >= 100 ? Math.round(val) : val >= 10 ? Math.round(val * 10) / 10 : Math.round(val * 100) / 100;
      return rounded + ' ' + units[idx];
    },

    formatDate(iso) {
      if (!iso) return '';
      try {
        const d = new Date(iso);
        if (isNaN(d.getTime())) return '';
        const y = d.getFullYear();
        const m = String(d.getMonth() + 1).padStart(2, '0');
        const day = String(d.getDate()).padStart(2, '0');
        return `${y}-${m}-${day}`;
      } catch (e) {
        return '';
      }
    },

    downloadBook(stem) {
      window.location.href = '/api/library/' + encodeURIComponent(stem) + '/download';
    },

    async deleteBook(stem) {
      if (!stem) return;
      if (!confirm(`确定要删除《${stem}》吗？此操作不可撤销。`)) return;
      try {
        const resp = await fetch('/api/library/' + encodeURIComponent(stem), { method: 'DELETE' });
        if (resp.ok) {
          await this.loadLibrary();
        } else {
          console.error('Delete failed:', resp.status, resp.statusText);
          alert('删除失败：' + resp.statusText);
        }
      } catch (e) {
        console.error('Delete failed:', e);
        alert('删除失败：' + e.message);
      }
    },

    editBook(stem) {
      window.location.href = '/pages/edit?book=' + encodeURIComponent(stem);
    },

    canEdit(stem) {
      if (!stem) return false;
      return this.workspaceBooks.some(b => b.stem === stem && b.has_book_md);
    },

    async refresh() {
      this.loading = true;
      await Promise.all([this.loadLibrary(), this.loadBooks()]);
      this.loading = false;
    },
  };
}
