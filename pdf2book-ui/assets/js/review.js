// PDF2BOOK Review Page — Alpine.js component
// Loads book list + book.md content + review issues from the backend.
// The "before" panel shows the actual book.md with low-confidence blocks
// and title issues highlighted. The "after" panel stays as a placeholder
// because the AI correction itself runs in the conversion pipeline (or
// via the Skill path), not via a standalone API call.

function reviewPage() {
  return {
    books: [],
    selectedStem: '',
    bookMd: '',
    metaMd: '',
    issues: null,
    loadingBooks: true,
    loadingContent: false,
    loadingIssues: false,
    error: '',

    async init() {
      await this.loadBooks();
    },

    async loadBooks() {
      this.loadingBooks = true;
      this.error = '';
      try {
        const res = await fetch('/api/books');
        const data = await res.json();
        this.books = (data.books || []).filter(b => b.has_book_md);
        if (this.books.length > 0) {
          this.selectedStem = this.books[0].stem;
          await this.loadBook();
        }
      } catch (e) {
        this.error = '加载书目列表失败：' + e.message;
      } finally {
        this.loadingBooks = false;
      }
    },

    async loadBook() {
      if (!this.selectedStem) return;
      this.loadingContent = true;
      this.loadingIssues = true;
      this.error = '';
      this.bookMd = '';
      this.issues = null;
      try {
        const [bookRes, issuesRes] = await Promise.allSettled([
          fetch(`/api/books/${encodeURIComponent(this.selectedStem)}`).then(r => r.json()),
          fetch(`/api/review/${encodeURIComponent(this.selectedStem)}/issues`).then(r => r.json()),
        ]);
        if (bookRes.status === 'fulfilled') {
          this.bookMd = bookRes.value.book_md || '';
          this.metaMd = bookRes.value.meta_md || '';
        }
        if (issuesRes.status === 'fulfilled') {
          this.issues = issuesRes.value;
        }
      } catch (e) {
        this.error = '加载内容失败：' + e.message;
      } finally {
        this.loadingContent = false;
        this.loadingIssues = false;
      }
    },

    onBookChange() {
      this.loadBook();
    },

    // Build highlighted book.md lines for display in the "before" panel.
    // Returns an array of { num, text, isLowConf, isTitleIssue, issueType }
    get bookLines() {
      if (!this.bookMd) return [];
      const lines = this.bookMd.split('\n');
      const lowConfLines = new Set();
      const titleIssueLines = new Map(); // line -> issue description
      if (this.issues) {
        for (const item of this.issues.low_confidence_texts || []) {
          lowConfLines.add(item.line);
        }
        for (const item of this.issues.title_candidates || []) {
          titleIssueLines.set(item.line, item.issue || '标题问题');
        }
      }
      return lines.map((text, i) => ({
        num: i + 1,
        text,
        isLowConf: lowConfLines.has(i),
        isTitleIssue: titleIssueLines.has(i),
        issueType: titleIssueLines.get(i) || '',
      }));
    },

    get totalIssues() {
      return this.issues?.total_issues || 0;
    },

    get lowConfCount() {
      return (this.issues?.low_confidence_texts || []).length;
    },

    get titleIssueCount() {
      return (this.issues?.title_candidates || []).length;
    },

    get paraIssueCount() {
      return (this.issues?.paragraph_issues || []).length;
    },

    get chapterIssueCount() {
      return (this.issues?.chapter_structure_issues || []).length;
    },

    get tocIssueCount() {
      return (this.issues?.toc_issues || []).length;
    },

    // The "after" panel message — explains where AI review actually runs
    get afterPanelMessage() {
      if (!this.bookMd) return '请先加载书目';
      if (this.totalIssues === 0) return '当前 book.md 未检测到需要 AI 审查的问题';
      return 'AI 审查在转换流程的 ai_review 阶段自动运行，或通过 Skill 路径由 agent 执行。此面板显示修正前内容供人工核对。';
    },

    refresh() {
      this.loadBook();
    },
  };
}
