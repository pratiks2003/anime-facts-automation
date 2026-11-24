import { LightningElement, wire } from 'lwc';
import latestReels from '@salesforce/apex/PublicSiteController.latestReels';
import weeklyDrops from '@salesforce/apex/PublicSiteController.weeklyDrops';
import facts from '@salesforce/apex/PublicSiteController.facts';
import ideas from '@salesforce/apex/PublicSiteController.ideas';
import vote from '@salesforce/apex/PublicSiteController.vote';
import suggest from '@salesforce/apex/PublicSiteController.suggest';
import { errorMessage, instagramEmbedUrl } from 'c/instaUtils';

const INSTAGRAM = 'https://www.instagram.com/senpai.secrets.official/';
const VOTED_KEY = 'senpai-voted-ideas';

export default class InstaPublicSite extends LightningElement {
    instagramUrl = INSTAGRAM;
    reels = [];
    drops = [];
    allFacts = [];
    ideaList = [];
    query = '';
    animeFilter = 'All';
    suggestAnime = '';
    suggestIdea = '';
    suggestMessage;
    suggestError;
    voteError;
    loadingFacts = true;
    dropsError;
    voted = new Set();

    connectedCallback() {
        try {
            this.voted = new Set(JSON.parse(window.localStorage.getItem(VOTED_KEY) || '[]'));
        } catch (e) {
            this.voted = new Set();
        }
    }

    @wire(latestReels, { limitSize: 6 })
    wiredReels({ data }) {
        if (data) {
            this.reels = data
                .map((r, i) => ({ ...r, key: `reel-${i}`, embed: instagramEmbedUrl(r.permalink) }))
                .filter((r) => r.embed);
        }
    }

    @wire(weeklyDrops)
    wiredDrops({ data, error }) {
        if (data) {
            this.drops = data.map((d, i) => ({ ...d, key: `drop-${i}` }));
        } else if (error) {
            this.dropsError = errorMessage(error);
        }
    }

    @wire(facts)
    wiredFacts({ data }) {
        if (data) {
            this.allFacts = data;
            this.loadingFacts = false;
        }
    }

    @wire(ideas)
    wiredIdeas({ data }) {
        if (data) {
            this.ideaList = data;
        }
    }

    get hasReels() {
        return this.reels.length > 0;
    }
    get hasDrops() {
        return this.drops.length > 0;
    }

    get animeOptions() {
        const names = [...new Set(this.allFacts.map((f) => f.anime))].sort();
        return ['All', ...names].map((n) => ({ value: n, className: n === this.animeFilter ? 'chip chip_on' : 'chip' }));
    }

    get visibleFacts() {
        const q = this.query.trim().toLowerCase();
        return this.allFacts
            .filter((f) => this.animeFilter === 'All' || f.anime === this.animeFilter)
            .filter((f) => !q || `${f.title} ${f.content} ${f.anime}`.toLowerCase().includes(q))
            .slice(0, 24);
    }

    get factCount() {
        return this.allFacts.length;
    }

    get hasFacts() {
        return this.visibleFacts.length > 0;
    }

    get ideaCards() {
        return this.ideaList.map((i) => ({ ...i, voted: this.voted.has(i.id), label: this.voted.has(i.id) ? 'Voted ✓' : 'Vote' }));
    }

    get hasIdeas() {
        return this.ideaList.length > 0;
    }

    handleQuery(event) {
        this.query = event.target.value;
    }

    handleAnime(event) {
        this.animeFilter = event.currentTarget.dataset.value;
    }

    async handleVote(event) {
        const id = event.currentTarget.dataset.id;
        if (this.voted.has(id)) {
            return;
        }
        this.voteError = undefined;
        try {
            const votes = await vote({ ideaId: id });
            this.ideaList = this.ideaList.map((i) => (i.id === id ? { ...i, votes } : i));
            this.voted = new Set([...this.voted, id]);
            try {
                window.localStorage.setItem(VOTED_KEY, JSON.stringify([...this.voted]));
            } catch (e) {
                // private mode: the vote still counts, we just can't remember it
            }
        } catch (e) {
            this.voteError = errorMessage(e);
        }
    }

    handleSuggestAnime(event) {
        this.suggestAnime = event.target.value;
    }

    handleSuggestIdea(event) {
        this.suggestIdea = event.target.value;
    }

    async handleSuggest() {
        this.suggestMessage = undefined;
        this.suggestError = undefined;
        try {
            await suggest({ anime: this.suggestAnime, idea: this.suggestIdea });
            this.suggestMessage = 'Thanks! Senpai will review your idea 👀';
            this.suggestAnime = '';
            this.suggestIdea = '';
        } catch (e) {
            this.suggestError = errorMessage(e);
        }
    }
}
