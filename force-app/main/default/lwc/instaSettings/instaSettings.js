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
        help: 'Adds the Evening slot (news & hype formats) to the Morning slot.'
    },
    {
        key: 'autoApprove',
        label: 'Auto-approve',
        help: 'Publish as soon as a Reel renders and passes checks. Off = wait for your approval here.'
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

    get eveningDisabled() {
        return !this.values.twoReelsPerDay;
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
