import { LightningElement, api } from 'lwc';
import createReelNow from '@salesforce/apex/AdminPanelController.createReelNow';
import getFormats from '@salesforce/apex/AdminPanelController.getFormats';
import { formatIst, relativeTime, compactNumber, statusClass, errorMessage } from 'c/instaUtils';

export default class InstaDashboard extends LightningElement {
    @api overview;
    formats = [];
    newFormat = 'Fact';
    newTopic = '';
    creating = false;
    message;
    error;

    async connectedCallback() {
        try {
            this.formats = await getFormats();
        } catch (e) {
            this.error = errorMessage(e);
        }
    }

    get kpis() {
        const c = (this.overview && this.overview.counts) || {};
        const posts = (this.overview && this.overview.posts) || [];
        const views = posts.reduce((sum, p) => sum + (p.Views__c || 0), 0);
        const inFlight = (c.Queued || 0) + (c.Generating || 0) + (c.Publishing || 0);
        return [
            { key: 'pub', label: 'Published', value: c.Published || 0, tone: 'kpi kpi_good' },
            { key: 'flight', label: 'In progress', value: inFlight, tone: 'kpi kpi_info' },
            { key: 'review', label: 'Held for review', value: (this.overview && this.overview.held) || 0, tone: 'kpi kpi_warn' },
            { key: 'fail', label: 'Failed', value: c.Failed || 0, tone: 'kpi kpi_bad' },
            { key: 'views', label: 'Views (recent posts)', value: compactNumber(views), tone: 'kpi' }
        ];
    }

    get upcoming() {
        return ((this.overview && this.overview.upcoming) || []).map((u, i) => ({
            key: `${u.slot}-${i}`,
            slot: u.slot,
            format: u.format || 'Fact',
            when: formatIst(u.at),
            relative: relativeTime(u.at)
        }));
    }

    get jobs() {
        return ((this.overview && this.overview.jobs) || []).map((j) => ({
            ...j,
            reel: j.Content__r ? j.Content__r.Name : '—',
            badge: statusClass(j.Status__c),
            when: formatIst(j.Dispatched_At__c),
            hasRun: !!j.Run_URL__c
        }));
    }

    get hasJobs() {
        return this.jobs.length > 0;
    }

    get schedulerText() {
        const s = this.overview && this.overview.scheduler;
        if (!s || !s.running) {
            return 'Scheduler not running — turn Automation on in Settings.';
        }
        return `Scheduler heartbeat next at ${formatIst(s.nextRun)} (${relativeTime(s.nextRun)}).`;
    }

    get formatOptions() {
        return this.formats.map((f) => ({ value: f, label: f, selected: f === this.newFormat }));
    }

    handleFormat(event) {
        this.newFormat = event.target.value;
    }

    handleTopic(event) {
        this.newTopic = event.target.value;
    }

    async handleCreate() {
        this.creating = true;
        this.message = undefined;
        this.error = undefined;
        try {
            await createReelNow({ format: this.newFormat, topic: this.newTopic });
            this.message = `Queued a ${this.newFormat} Reel. GitHub is generating it now.`;
            this.newTopic = '';
            this.dispatchEvent(new CustomEvent('changed'));
        } catch (e) {
            this.error = errorMessage(e);
        } finally {
            this.creating = false;
        }
    }
}
