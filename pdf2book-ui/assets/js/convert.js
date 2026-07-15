// PDF2BOOK Convert Page — Alpine.js component
// Handles: inbox PDF listing, conversion triggering, progress polling

function convertPage() {
  return {
    // Data
    inboxFiles: [],
    selectedStem: '',
    resume: true,
    aiReview: false,
    loading: false,
    converting: false,
    status: null,
    pollTimer: null,
    viewMode: 'idle',  // 'idle' | 'converting' | 'completed' | 'failed'

    // Stage definitions for the checklist
    stages: [
      { key: 'ocr', label: 'OCR识别', detail: '' },
      { key: 'postprocess', label: '页面分类与后处理', detail: '' },
      { key: 'markdown', label: 'Markdown生成', detail: '' },
      { key: 'ai_review', label: 'AI审查', detail: '' },
      { key: 'epub', label: 'EPUB构建', detail: '' },
    ],

    async init() {
      await this.loadInbox();
    },

    async loadInbox() {
      this.loading = true;
      try {
        const resp = await fetch('/api/inbox');
        const data = await resp.json();
        this.inboxFiles = data.files || [];
        if (this.inboxFiles.length > 0 && !this.selectedStem) {
          this.selectedStem = this.inboxFiles[0].stem;
        }
      } catch (e) {
        console.error('Failed to load inbox:', e);
      } finally {
        this.loading = false;
      }
    },

    // Start conversion
    async startConvert() {
      if (!this.selectedStem || this.converting) return;
      this.converting = true;
      this.viewMode = 'converting';
      try {
        const resp = await fetch('/api/convert', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            stem: this.selectedStem,
            resume: this.resume,
            ai_review: this.aiReview,
          }),
        });
        if (!resp.ok) {
          const err = await resp.json();
          throw new Error(err.detail || 'Failed to start conversion');
        }
        this.status = await resp.json();
        this.startPolling();
      } catch (e) {
        console.error('Conversion start failed:', e);
        this.viewMode = 'failed';
        this.converting = false;
        this.status = { status: 'failed', message: e.message, error: e.message, logs: [] };
      }
    },

    startPolling() {
      if (this.pollTimer) clearInterval(this.pollTimer);
      this.pollTimer = setInterval(() => this.pollStatus(), 2000);
    },

    stopPolling() {
      if (this.pollTimer) {
        clearInterval(this.pollTimer);
        this.pollTimer = null;
      }
    },

    async pollStatus() {
      if (!this.selectedStem) return;
      try {
        const resp = await fetch(`/api/convert/${encodeURIComponent(this.selectedStem)}/status`);
        if (resp.status === 404) return;
        this.status = await resp.json();
        if (this.status.status === 'completed' || this.status.status === 'failed') {
          this.stopPolling();
          this.converting = false;
          this.viewMode = this.status.status;
        }
        this.updateStages();
      } catch (e) {
        console.error('Status poll failed:', e);
      }
    },

    updateStages() {
      if (!this.status) return;
      const currentStage = this.status.stage;
      for (const s of this.stages) {
        if (this.status.status === 'completed') {
          s.state = 'done';
          s.detail = '完成';
        } else if (s.key === currentStage) {
          s.state = 'active';
          s.detail = this.status.message || '进行中...';
        } else if (this.stageIndex(s.key) < this.stageIndex(currentStage)) {
          s.state = 'done';
          s.detail = '完成';
        } else {
          s.state = 'pending';
          s.detail = '等待中';
        }
      }
      // AI review stage: skip if not enabled
      if (!this.aiReview) {
        const aiStage = this.stages.find(s => s.key === 'ai_review');
        if (aiStage) {
          aiStage.state = 'skipped';
          aiStage.detail = '已跳过';
        }
      }
    },

    stageIndex(key) {
      return this.stages.findIndex(s => s.key === key);
    },

    // Get progress percentage
    get progressPct() {
      if (!this.status) return 0;
      return this.status.progress || 0;
    },

    // Get recent log lines (last 8)
    get recentLogs() {
      if (!this.status || !this.status.logs) return [];
      return this.status.logs.slice(-8);
    },

    // Format file size
    formatSize(bytes) {
      if (bytes < 1024) return bytes + ' B';
      if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
      return (bytes / 1024 / 1024).toFixed(1) + ' MB';
    },

    // Format timestamp
    formatDate(iso) {
      if (!iso) return '';
      const d = new Date(iso);
      return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
    },

    // Get selected file info
    get selectedFile() {
      return this.inboxFiles.find(f => f.stem === this.selectedStem);
    },

    // Check if a stage is done
    isStageDone(stageKey) {
      const s = this.stages.find(s => s.key === stageKey);
      return s && s.state === 'done';
    },

    // Check if a stage is active
    isStageActive(stageKey) {
      const s = this.stages.find(s => s.key === stageKey);
      return s && s.state === 'active';
    },

    // Navigate to edit page
    goToEdit() {
      if (this.selectedStem) {
        window.location.href = `/pages/edit?book=${encodeURIComponent(this.selectedStem)}`;
      }
    },

    // Navigate to library page
    goToLibrary() {
      window.location.href = '/pages/library';
    },

    // Reset to idle state
    resetConvert() {
      this.stopPolling();
      this.converting = false;
      this.viewMode = 'idle';
      this.status = null;
      for (const s of this.stages) {
        s.state = 'pending';
        s.detail = '';
      }
    },
  };
}
