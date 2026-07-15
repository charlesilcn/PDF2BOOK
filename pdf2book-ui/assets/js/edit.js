// PDF2BOOK Edit Page — Alpine.js component
// Handles: book loading, module rendering, preview, layout toggling, save

function editPage() {
  return {
    books: [],
    currentBook: '',
    modules: [],
    selectedId: null,
    renderedPreview: '',
    totalWords: 0,
    viewMode: 'split',  // 'edit' | 'split' | 'preview' — controls panel visibility

    async init() {
      await this.loadBooks();
      // Watch for module changes → update preview
      this.$watch('modules', () => this.updatePreview(), { deep: true });
      // Auto-load book from URL query param (?book=xxx)
      const params = new URLSearchParams(window.location.search);
      const bookStem = params.get('book');
      if (bookStem) {
        await this.loadBook(bookStem);
      }
    },

    async loadBooks() {
      try {
        const resp = await fetch('/api/books');
        const data = await resp.json();
        this.books = data.books.filter(b => b.has_book_md);
      } catch (e) {
        console.error('Failed to load books:', e);
      }
    },

    async loadBook(stem) {
      if (!stem) return;
      try {
        const resp = await fetch(`/api/books/${encodeURIComponent(stem)}/modules`);
        const data = await resp.json();
        this.currentBook = stem;
        this.modules = data.modules;
        this.totalWords = this.modules.reduce((sum, m) => sum + m.word_count, 0);
        if (this.modules.length > 0) {
          this.selectedId = this.modules[0].id;
        }
        this.updatePreview();
      } catch (e) {
        console.error('Failed to load book modules:', e);
      }
    },

    selectModule(id) {
      this.selectedId = id;
    },

    get selectedModule() {
      return this.modules.find(m => m.id === this.selectedId);
    },

    // Toggle a layout class on the selected module
    toggleLayoutClass(className) {
      const mod = this.selectedModule;
      if (!mod) return;
      const idx = mod.layout_classes.indexOf(className);
      if (idx >= 0) {
        mod.layout_classes.splice(idx, 1);
      } else {
        mod.layout_classes.push(className);
      }
    },

    // Check if selected module has a layout class
    hasLayoutClass(className) {
      const mod = this.selectedModule;
      return mod && mod.layout_classes.includes(className);
    },

    // Set alignment (exclusive — only one at a time)
    setAlignment(align) {
      const mod = this.selectedModule;
      if (!mod) return;
      // Remove existing alignment classes
      mod.layout_classes = mod.layout_classes.filter(
        c => !c.startsWith('align-')
      );
      if (align !== 'justify') {
        mod.layout_classes.push(`align-${align}`);
      }
    },

    // Set spacing (exclusive)
    setSpacing(spacing) {
      const mod = this.selectedModule;
      if (!mod) return;
      mod.layout_classes = mod.layout_classes.filter(
        c => !c.startsWith('spacing-')
      );
      if (spacing !== 'normal') {
        mod.layout_classes.push(`spacing-${spacing}`);
      }
    },

    // Delete a module
    deleteModule(idx) {
      this.modules.splice(idx, 1);
      this.totalWords = this.modules.reduce((sum, m) => sum + m.word_count, 0);
    },

    // Render preview from modules
    updatePreview() {
      const md = this.modulesToMarkdown();
      let html = marked.parse(md);
      // Rewrite relative image URLs to use the workspace file API
      // book.md references images as "images/p6_e0.png" or "pages/page_0001.png"
      // (relative paths), but the edit page is at /pages/edit, so the browser
      // would resolve them against /pages/ → 404. Rewrite to /api/books/{stem}/raw/{path}
      if (this.currentBook) {
        const base = `/api/books/${encodeURIComponent(this.currentBook)}/raw/`;
        html = html.replace(
          /src="(?!https?:\/\/|\/|data:)([^"]+)"/g,
          `src="${base}$1"`
        );
      }
      this.renderedPreview = html;
    },

    // Convert modules to Markdown for preview rendering
    modulesToMarkdown() {
      let lines = [];
      let inChapter = false;
      for (const mod of this.modules) {
        const content = mod.content.trim();
        if (mod.heading_level === 1) {
          if (inChapter) lines.push(':::');
          lines.push('::: {.chapter}');
          lines.push(content);
          inChapter = true;
        } else if (mod.layout_classes.length > 0) {
          const cls = mod.layout_classes.map(c => '.' + c).join(' ');
          lines.push(`::: {${cls}}`);
          lines.push(content);
          lines.push(':::');
        } else {
          lines.push(content);
        }
      }
      if (inChapter) lines.push(':::');
      return lines.join('\n\n');
    },

    // Save modules to server
    async saveModules() {
      if (!this.currentBook) return;
      try {
        const resp = await fetch(`/api/books/${encodeURIComponent(this.currentBook)}/modules`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ modules: this.modules }),
        });
        const data = await resp.json();
        if (data.status === 'ok') {
          console.log('Saved:', data.module_count, 'modules');
        }
      } catch (e) {
        console.error('Save failed:', e);
      }
    },

    // Get display label for module type
    typeLabel(type) {
      const labels = {
        chapter: '章节',
        paragraph: '正文段落',
        image: '图片',
        cover: '封面',
        divider: '分隔',
        quote: '引文',
        dialogue: '对话',
        toc: '目录',
        other: '其他',
      };
      return labels[type] || type;
    },

    // Split selected module at cursor position (simplified: split in half)
    splitModule() {
      const mod = this.selectedModule;
      if (!mod || mod.type !== 'paragraph') return;
      const idx = this.modules.findIndex(m => m.id === mod.id);
      const content = mod.content.trim();
      const mid = Math.floor(content.length / 2);
      // Find nearest sentence break
      let splitPos = content.indexOf('。', mid);
      if (splitPos === -1 || splitPos > mid + 50) splitPos = mid;
      splitPos += 1; // Include the period in the first half

      const firstHalf = content.substring(0, splitPos).trim();
      const secondHalf = content.substring(splitPos).trim();

      mod.content = firstHalf;
      const newMod = {
        id: 'm' + (this.modules.length + 1),
        type: 'paragraph',
        content: secondHalf,
        layout_classes: [...mod.layout_classes],
        word_count: secondHalf.length,
        heading_level: null,
        heading_id: null,
      };
      this.modules.splice(idx + 1, 0, newMod);
      this.selectedId = newMod.id;
      this.totalWords = this.modules.reduce((sum, m) => sum + m.word_count, 0);
    },

    // Merge selected module with previous
    mergeModule() {
      const mod = this.selectedModule;
      if (!mod) return;
      const idx = this.modules.findIndex(m => m.id === mod.id);
      if (idx === 0) return;
      const prev = this.modules[idx - 1];
      if (prev.type !== 'paragraph' && prev.type !== 'dialogue') return;

      prev.content = prev.content.trim() + '\n\n' + mod.content.trim();
      prev.word_count = prev.content.length;
      this.modules.splice(idx, 1);
      this.selectedId = prev.id;
      this.totalWords = this.modules.reduce((sum, m) => sum + m.word_count, 0);
    },

    // Delete selected module
    deleteSelectedModule() {
      const mod = this.selectedModule;
      if (!mod) return;
      const idx = this.modules.findIndex(m => m.id === mod.id);
      this.modules.splice(idx, 1);
      this.selectedId = this.modules.length > 0
        ? this.modules[Math.min(idx, this.modules.length - 1)].id
        : null;
      this.totalWords = this.modules.reduce((sum, m) => sum + m.word_count, 0);
    },
  };
}
