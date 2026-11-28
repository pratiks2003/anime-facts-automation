import { LightningElement, track } from 'lwc';
import getOverview from '@salesforce/apex/AdminPanelController.getOverview';
import { errorMessage } from 'c/instaUtils';

const TABS = [
    { id: 'mission', label: 'Mission Control' },
    { id: 'dashboard', label: 'Dashboard' },
    { id: 'reels', label: 'Reels' },
    { id: 'published', label: 'Published' },
    { id: 'comments', label: 'Comments' },
    { id: 'ideas', label: 'Ideas & Insights' },
    { id: 'learn', label: 'Learn & Trends' },
    { id: 'settings', label: 'Settings' }
];
const REFRESH_MS = 30000;

export default class InstaAdminApp extends LightningElement {
    @track overview;
    activeTab = 'mission';
    loading = true;
    error;
    timer;

    connectedCallback() {
        this.refresh();
        // Light polling so job progress shows up without a page reload.
        this.timer = setInterval(() => this.refresh(true), REFRESH_MS); // eslint-disable-line @lwc/lwc/no-async-operation
    }

    disconnectedCallback() {
        clearInterval(this.timer);
    }

    async refresh(silent = false) {
        if (!silent) {
            this.loading = true;
        }
        try {
            this.overview = await getOverview();
            this.error = undefined;
        } catch (e) {
            this.error = errorMessage(e);
        } finally {
            this.loading = false;
        }
    }

    handleTab(event) {
        this.activeTab = event.currentTarget.dataset.tab;
    }

    handleChanged() {
        this.refresh(true);
    }

    get tabs() {
        return TABS.map((t) => ({ ...t, className: t.id === this.activeTab ? 'tab tab_active' : 'tab' }));
    }

    get settings() {
        return this.overview ? this.overview.settings : {};
    }

    get isMission() {
        return this.activeTab === 'mission';
    }
    get showHeader() {
        return !this.isMission;
    }
    get isDashboard() {
        return this.activeTab === 'dashboard';
    }
    get isReels() {
        return this.activeTab === 'reels';
    }
    get isPublished() {
        return this.activeTab === 'published';
    }
    get isComments() {
        return this.activeTab === 'comments';
    }
    get isIdeas() {
        return this.activeTab === 'ideas';
    }
    get isLearn() {
        return this.activeTab === 'learn';
    }
    get isSettings() {
        return this.activeTab === 'settings';
    }

    get automationChip() {
        return this.settings.automationEnabled ? 'chip chip_on' : 'chip chip_off';
    }
    get automationLabel() {
        return this.settings.automationEnabled ? 'Automation ON' : 'Automation OFF';
    }
    get cadenceLabel() {
        if (!this.settings.twoReelsPerDay) {
            return '1 Reel / day';
        }
        return this.settings.threeReelsPerDay ? '3 Reels / day' : '2 Reels / day';
    }
    get showDryRun() {
        return this.settings.dryRun;
    }
    get showAutoApprove() {
        return this.settings.autoApprove;
    }
}
