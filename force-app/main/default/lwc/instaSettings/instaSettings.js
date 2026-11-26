import { LightningElement, api } from 'lwc';
import saveSettings from '@salesforce/apex/AdminPanelController.saveSettings';
import { errorMessage } from 'c/instaUtils';

const TOGGLES = [
    {
        key: 'automationEnabled',
        label: 'Automation',
        help: 'Generate and post Reels on schedule. Off = nothing runs automatically.'
    },
    {
        key: 'twoReelsPerDay',
        label: '2 Reels per day',
        help: 'Adds the Evening slot to the Morning slot. Formats are chosen each time from what is trending.'
    },
    {
        key: 'threeReelsPerDay',
        label: '3 Reels per day',
        help: 'Adds a Midday slot between Morning and Evening (needs 2 Reels per day).'
    },
    {
        key: 'autoApprove',
        label: 'Auto-approve',
        help: 'Publish as soon as a Reel renders and passes checks. Off = wait for your approval here.'
    },
    {
        key: 'autoReply',
        label: 'Auto-reply comments',
        help: 'Senpai replies to every comment in a fun way (anime jokes for trolls). Spam and hateful comments are never answered.'
    },
    {
        key: 'trialReels',
        label: 'Trial Reels',
        help: 'Reels scoring 7-8 go to non-followers first and reach followers only if they perform (needs Instagram eligibility).'
    },
    {
        key: 'postLimitEnabled',
        label: 'Post limit',
        help: 'Keeps posting at a safe pace (max posts per 24 h + minimum gap) so Meta does not flag "unusual activity". Turn off e.g. the day after testing.'
    },
    {
        key: 'dryRun',
        label: 'Dry run',
        help: 'Render everything but never post to Instagram. Turn off to go live.'
    }
];

export default class InstaSettings extends LightningElement {
    @api
    get settings() {
        return this.values;
    }
    set settings(value) {
        this.values = { ...(value || {}) };
        this.original = { ...(value || {}) };
    }

    values = {};
    original = {};
    saving = false;
    message;
    error;

    get toggles() {
        return TOGGLES.map((t) => ({
            ...t,
            checked: !!this.values[t.key],
            className: this.values[t.key] ? 'switch switch_on' : 'switch',
            state: this.values[t.key] ? 'On' : 'Off'
        }));
    }

    get dirty() {
        return JSON.stringify(this.values) !== JSON.stringify(this.original);
    }

    get saveDisabled() {
        return this.saving || !this.dirty;
    }

    get limitDisabled() {
        return !this.values.postLimitEnabled;
    }

    get postsUsedLabel() {
        const used = this.values.postsLast24h || 0;
        const max = this.values.dailyPostLimit || 6;
        return `${used} of ${max} posts used in the last 24 hours`;
    }

    get postsUsedClass() {
        return (this.values.postsLast24h || 0) >= (this.values.dailyPostLimit || 6) ? 'error small' : 'muted small';
    }

    handleNumber(event) {
        const value = parseInt(event.target.value, 10);
        this.values = { ...this.values, [event.target.dataset.key]: Number.isNaN(value) ? null : value };
        this.message = undefined;
    }

    get eveningDisabled() {
        return !this.values.twoReelsPerDay;
    }

    get middayDisabled() {
        return !(this.values.twoReelsPerDay && this.values.threeReelsPerDay);
    }

    get goingLive() {
        return this.values.automationEnabled && !this.values.dryRun;
    }

    handleToggle(event) {
        const key = event.currentTarget.dataset.key;
        this.values = { ...this.values, [key]: !this.values[key] };
        this.message = undefined;
    }

    handleTime(event) {
        this.values = { ...this.values, [event.target.dataset.key]: event.target.value };
        this.message = undefined;
    }

    async handleSave() {
        this.saving = true;
        this.error = undefined;
        try {
            const saved = await saveSettings({ values: this.values });
            this.values = { ...saved };
            this.original = { ...saved };
            this.message = 'Saved.';
            this.dispatchEvent(new CustomEvent('changed'));
        } catch (e) {
            this.error = errorMessage(e);
        } finally {
            this.saving = false;
        }
    }
}
