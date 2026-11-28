import { LightningElement, api } from 'lwc';
import askShowrunner from '@salesforce/apex/MissionControlController.askShowrunner';
import getHealth from '@salesforce/apex/AdminAppController.getHealth';
import getAdminOverview from '@salesforce/apex/AdminPanelController.getOverview';
import { errorText } from 'c/mcUtils';

const TOOLS = [
    { id: 'settings', label: 'Settings', sub: 'Automation, slots, posting limits', icon: '⚙️' },
    { id: 'queue', label: 'Reel queue', sub: 'Every Reel and its job', icon: '🎞️' },
    { id: 'published', label: 'Published posts', sub: 'Instagram posts and metrics', icon: '📤' },
    { id: 'comments', label: 'Comments', sub: 'Replies the Community Manager wrote', icon: '💬' },
    { id: 'ideas', label: 'Ideas & insights', sub: 'Fan ideas, performance reviews', icon: '💡' },
    { id: 'learn', label: 'Learn & trends', sub: 'Reference Reels, styles, research', icon: '🧪' },
    { id: 'classic', label: 'Classic dashboard', sub: 'The old overview page', icon: '🗂️' }
];
const SUGGEST = [
    'What should tonight\'s Reel be?',
    'Which show is winning and why?',
    'What did you change in the playbook this week?',
    'Is anything broken right now?'
];

export default class McMore extends LightningElement {
    @api overview;
    tool;
    adminOverview;
    toolError;
    health;
    healthError;
    question = '';
    thread = [];
    asking = false;

    connectedCallback() {
        this.loadHealth();
    }

    async loadHealth() {
        try {
            this.health = await getHealth();
            this.healthError = undefined;
        } catch (e) {
            this.healthError = errorText(e);
        }
    }

    // ------------------------------------------------------------ ask
    get suggestions() { return SUGGEST.map((q, i) => ({ key: 's' + i, q })); }
    get hasThread() { return this.thread.length > 0; }
    get askDisabled() { return this.asking || !this.question.trim(); }
    onQuestion(event) { this.question = event.target.value; }
    useSuggestion(event) {
        this.question = event.currentTarget.dataset.q;
        this.ask();
    }
    onKey(event) {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            this.ask();
        }
    }
    async ask() {
        const q = this.question.trim();
        if (!q || this.asking) return;
        this.asking = true;
        this.thread = [...this.thread, { key: 'q' + this.thread.length, cls: 'msg me', text: q }];
        this.question = '';
        try {
            const a = await askShowrunner({ question: q });
            this.thread = [...this.thread, { key: 'a' + this.thread.length, cls: 'msg them', text: a || '(no answer)' }];
        } catch (e) {
            this.thread = [...this.thread, { key: 'e' + this.thread.length, cls: 'msg them err', text: errorText(e) }];
        } finally {
            this.asking = false;
        }
    }

    // ------------------------------------------------------------ health
    get checks() {
        const r = (this.health && this.health.report) || {};
        const p = (this.health && this.health.posting) || {};
        const qa = r.quality_checker_last_2_days || {};
        const item = (key, label, value, ok) => ({ key, label, value, cls: ok === null ? 'hdot' : ok ? 'hdot ok' : 'hdot bad' });
        const h = (v) => (v === null || v === undefined ? 'never' : v + ' h ago');
        return [
            item('m', 'Instagram metrics', h(r.metrics_hours_old), r.metrics_hours_old === null || r.metrics_hours_old === undefined ? null : r.metrics_hours_old <= 8),
            item('t', 'Trend research', h(r.trends_hours_old), r.trends_hours_old === null || r.trends_hours_old === undefined ? null : r.trends_hours_old <= 8),
            item('f', 'Failed jobs (24 h)', dash(r.failed_jobs_24h), (r.failed_jobs_24h || 0) === 0),
            item('k', 'Verified facts', dash(r.verified_facts), (r.verified_facts || 0) > 50),
            item('p', 'Posts (24 h)', `${dash(p.posts_last_24h !== undefined ? p.posts_last_24h : r.posts_24h)} / ${dash(p.max_per_day)}`, null),
            item('l', 'Posting limit', p.enabled ? `on · ${p.min_gap_minutes} min gap` : 'off', !!p.enabled),
            item('q', 'Quality checker (2 days)', `${qa.checked || 0} checked · ${qa.not_checked || 0} not`, (qa.not_checked || 0) === 0),
            item('u', 'Reels without an agent plan (7 d)', dash(r.reels_without_agent_plan_7d), (r.reels_without_agent_plan_7d || 0) === 0)
        ];
    }

    // ------------------------------------------------------------ tools
    get tools() { return TOOLS; }
    get inTool() { return !!this.tool; }
    get toolTitle() { const t = TOOLS.find((x) => x.id === this.tool); return t ? t.label : ''; }
    get isSettings() { return this.tool === 'settings'; }
    get isQueue() { return this.tool === 'queue'; }
    get isPublished() { return this.tool === 'published'; }
    get isComments() { return this.tool === 'comments'; }
    get isIdeas() { return this.tool === 'ideas'; }
    get isLearn() { return this.tool === 'learn'; }
    get isClassic() { return this.tool === 'classic'; }
    get adminSettings() { return (this.adminOverview && this.adminOverview.settings) || {}; }
    get needsAdmin() { return this.isSettings || this.isClassic; }
    get adminReady() { return !!this.adminOverview; }

    async openTool(event) {
        this.tool = event.currentTarget.dataset.id;
        if ((this.tool === 'settings' || this.tool === 'classic') && !this.adminOverview) await this.loadAdmin();
        if (window.scrollTo) window.scrollTo({ top: 0 });
    }
    back() { this.tool = undefined; }
    async loadAdmin() {
        try {
            this.adminOverview = await getAdminOverview();
            this.toolError = undefined;
        } catch (e) {
            this.toolError = errorText(e);
        }
    }
    handleChanged() { this.loadAdmin(); }
}

function dash(v) {
    return v === null || v === undefined ? '–' : String(v);
}
