import { LightningElement } from 'lwc';
import getResearch from '@salesforce/apex/AdminPanelController.getResearch';
import addReference from '@salesforce/apex/AdminPanelController.addReference';
import setStyleStatus from '@salesforce/apex/AdminPanelController.setStyleStatus';
import { errorMessage, formatIst } from 'c/instaUtils';

const BADGES = { Done: 'badge badge_good', Active: 'badge badge_good', Failed: 'badge badge_bad', Retired: 'badge badge_bad' };

export default class InstaLearn extends LightningElement {
    references = [];
    styles = [];
    brief;
    briefJson = {};
    url = '';
    notes = '';
    loading = true;
    submitting = false;
    message;
    error;
    timer;

    connectedCallback() {
        this.load();
        // analysis runs on GitHub (~2-4 min): refresh while anything is in flight
        this.timer = setInterval(() => {
            if (this.references.some((r) => r.Status__c === 'Queued' || r.Status__c === 'Analyzing')) {
                this.load();
            }
        }, 20000);
    }

    disconnectedCallback() {
        clearInterval(this.timer);
    }

    async load() {
        try {
            const data = await getResearch();
            this.references = data.references.map((r) => ({
                ...r,
                when: formatIst(r.Analyzed_At__c || r.CreatedDate),
                styleName: r.Style__r ? r.Style__r.Name : null,
                lessons: r.Lessons__c ? r.Lessons__c.split('\n').filter((l) => l.trim()) : [],
                badge: BADGES[r.Status__c] || 'badge badge_warn'
            }));
            this.styles = data.styles.map((s) => ({
                ...s,
                reels: s.Reels__c || 0,
                views: s.Avg_Views__c == null ? '-' : s.Avg_Views__c,
                watch: s.Avg_Watch_Seconds__c == null ? '-' : `${s.Avg_Watch_Seconds__c}s`,
                score: s.Performance_Score__c == null ? 'not scored yet' : `${s.Performance_Score__c}x channel avg`,
                badge: BADGES[s.Status__c] || 'badge badge_warn',
                canActivate: s.Status__c !== 'Active',
                canRetire: s.Status__c !== 'Retired'
            }));
            this.brief = data.brief && data.brief.length ? data.brief[0] : null;
            try {
                this.briefJson = this.brief && this.brief.Brief_JSON__c ? JSON.parse(this.brief.Brief_JSON__c) : {};
            } catch (e) {
                this.briefJson = {};
            }
            this.error = undefined;
        } catch (e) {
            this.error = errorMessage(e);
        } finally {
            this.loading = false;
        }
    }

    get hasReferences() {
        return this.references.length > 0;
    }

    get styleSignals() {
        return this.briefJson.style_signals || [];
    }

    get gaps() {
        return this.briefJson.our_gaps || [];
    }

    handleUrl(event) {
        this.url = event.target.value;
    }

    handleNotes(event) {
        this.notes = event.target.value;
    }

    async handleLearn() {
        this.submitting = true;
        this.message = undefined;
        this.error = undefined;
        try {
            await addReference({ url: this.url, notes: this.notes });
            this.url = '';
            this.notes = '';
            this.message = 'Queued. Analysis takes about 3 minutes.';
            await this.load();
        } catch (e) {
            this.error = errorMessage(e);
        } finally {
            this.submitting = false;
        }
    }

    async handleStyle(event) {
        try {
            await setStyleStatus({ styleId: event.currentTarget.dataset.id, status: event.currentTarget.dataset.status });
            await this.load();
        } catch (e) {
            this.error = errorMessage(e);
        }
    }
}
