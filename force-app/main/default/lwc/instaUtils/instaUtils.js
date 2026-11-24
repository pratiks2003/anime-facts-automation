// Shared helpers for the InstaAgent admin components.

const IST = 'Asia/Kolkata';

export function formatIst(value, withDate = true) {
    if (!value) {
        return '—';
    }
    const options = withDate
        ? { timeZone: IST, day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false }
        : { timeZone: IST, hour: '2-digit', minute: '2-digit', hour12: false };
    return new Intl.DateTimeFormat('en-IN', options).format(new Date(value)) + ' IST';
}

export function relativeTime(value) {
    if (!value) {
        return '';
    }
    const minutes = Math.round((new Date(value).getTime() - Date.now()) / 60000);
    const abs = Math.abs(minutes);
    const text = abs < 60 ? `${abs} min` : abs < 48 * 60 ? `${Math.round(abs / 60)} h` : `${Math.round(abs / 1440)} d`;
    return minutes >= 0 ? `in ${text}` : `${text} ago`;
}

export function compactNumber(value) {
    if (value === null || value === undefined) {
        return '—';
    }
    return new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(value);
}

export function errorMessage(error) {
    if (!error) {
        return 'Unknown error';
    }
    if (Array.isArray(error.body)) {
        return error.body.map((e) => e.message).join(', ');
    }
    return (error.body && error.body.message) || error.message || String(error);
}

const STATUS_TONES = {
    Planned: 'muted',
    Queued: 'muted',
    Generating: 'info',
    Rendered: 'warn',
    Approved: 'info',
    Publishing: 'info',
    Published: 'good',
    Failed: 'bad',
    Rejected: 'bad',
    Cancelled: 'muted',
    Dispatched: 'info',
    Running: 'info',
    Succeeded: 'good',
    'Timed Out': 'bad'
};

export function statusClass(status) {
    return `badge badge_${STATUS_TONES[status] || 'muted'}`;
}

/** https://www.instagram.com/reel/ABC123/ -> embeddable iframe URL. */
export function instagramEmbedUrl(permalink) {
    if (!permalink) {
        return null;
    }
    const match = permalink.match(/instagram\.com\/(reel|p|tv)\/([A-Za-z0-9_-]+)/);
    return match ? `https://www.instagram.com/${match[1]}/${match[2]}/embed/` : null;
}
