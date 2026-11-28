/**
 * The agents' own playbook: strategy settings they tune themselves (Agent_Playbook__c, one row per key).
 *
 * Guardrails in code: every value is validated against a range; every change is logged in Strategy_Change__c with
 * the baseline (average performance index of the 10 Reels before). review() compares the Reels since the change:
 * 20% worse after 4+ scored Reels -> rolled back automatically; after 8 Reels without a drop -> kept.
 */
public with sharing class Playbook {
    public static final Decimal ROLLBACK_DROP = 0.8;
    public static final Integer JUDGE_AFTER = 4;
    public static final Integer KEEP_AFTER = 8;

    /** key -> [default JSON, explanation] */
    public static final Map<String, List<String>> SPEC = new Map<String, List<String>>{
        'explore_share' => new List<String>{ '0.2', 'Share of slots used to test something new (0.05-0.5); the rest repeat what is winning.' },
        'drop_after_losers' => new List<String>{ '2', 'Drop a show after this many loser Reels in a row (1-4).' },
        'drop_hours' => new List<String>{ '48', 'How long a dropped show stays dropped, in hours (12-168).' },
        'slot_times' => new List<String>{ '{"Morning":"09:00","Midday":"13:30","Evening":"19:00"}',
            'Posting times IST (07:00-23:30, at least 3 h apart). Changing this moves the real schedule.' },
        'style_defaults' => new List<String>{ '{}', 'Default editor style for every Reel (keys from get_editor_capabilities); the Planner can still override per Reel.' },
        'focus_shows' => new List<String>{ '[]', 'Shows to prioritise (max 10).' },
        'avoid_shows' => new List<String>{ '[]', 'Shows to avoid (max 10).' },
        'format_mix' => new List<String>{ '{}', 'Preferred formats with weights 0-1, e.g. {"Fan Theory":0.4}.' },
        'hook_rules' => new List<String>{ '[]', 'Proven hook / editing rules backed by the scoreboard (max 8 short rules).' }
    };

    public static Map<String, Object> getAll() {
        Map<String, Object> out = new Map<String, Object>();
        Map<String, Agent_Playbook__c> rows = rows();
        for (String key : SPEC.keySet()) {
            Agent_Playbook__c r = rows.get(key);
            out.put(key, ShowrunnerSupport.parse(r == null || String.isBlank(r.Value__c) ? SPEC.get(key)[0] : r.Value__c));
        }
        return out;
    }

    public static Map<String, String> explanations() {
        Map<String, String> out = new Map<String, String>();
        for (String key : SPEC.keySet()) out.put(key, SPEC.get(key)[1]);
        return out;
    }

    public static Integer intValue(String key, Integer fallback) {
        try {
            Object v = getAll().get(key);
            return v == null ? fallback : Integer.valueOf(String.valueOf(v).split('\\.')[0]);
        } catch (Exception e) {
            return fallback;
        }
    }

    static Map<String, Agent_Playbook__c> rows() {
        Map<String, Agent_Playbook__c> out = new Map<String, Agent_Playbook__c>();
        for (Agent_Playbook__c r : [SELECT Id, Key__c, Value__c FROM Agent_Playbook__c WHERE Key__c IN :SPEC.keySet()]) {
            out.put(r.Key__c, r);
        }
        return out;
    }

    /** Validates and applies a change; returns a sentence for the agent ("Changed ..." or "Refused: ..."). */
    public static String change(String key, String valueJson, String reason, String agent) {
        if (!SPEC.containsKey(key)) {
            return 'Refused: unknown setting "' + key + '". Settings: ' + String.join(new List<String>(SPEC.keySet()), ', ') + '.';
        }
        Object value;
        try {
            value = JSON.deserializeUntyped(valueJson);
        } catch (Exception e) {
            return 'Refused: value must be JSON (e.g. 0.3, ["One Piece"], {"Morning":"08:30"}).';
        }
        String problem = validate(key, value);
        if (problem != null) {
            return 'Refused: ' + problem;
        }
        if (String.isBlank(reason)) {
            return 'Refused: give the reason / hypothesis with evidence from the scoreboard.';
        }
        Object old = getAll().get(key);
        String oldJson = JSON.serialize(old);
        String newJson = JSON.serialize(value);
        if (oldJson == newJson) {
            return 'No change: ' + key + ' is already ' + newJson + '.';
        }
        apply(key, value);
        insert new Strategy_Change__c(Name = (agent + ': ' + key).abbreviate(80), Setting__c = key, Old_Value__c = oldJson,
            New_Value__c = newJson, Reason__c = reason.abbreviate(5000), Changed_By__c = agent, Status__c = 'Active',
            Baseline_Index__c = Scoreboard.averageIndex(Datetime.now(), 10), Reels_Since__c = 0);
        ShowrunnerSupport.log(agent, 'Changed playbook', null, key + ': ' + oldJson + ' -> ' + newJson + '\nWhy: ' + reason, false, 'info');
        return 'Changed ' + key + ' to ' + newJson + '. It is reviewed automatically: if the next ' + JUDGE_AFTER
            + '+ Reels score 20% below the baseline, it rolls back.';
    }

    static void apply(String key, Object value) {
        Agent_Playbook__c row = rows().get(key);
        if (row == null) {
            row = new Agent_Playbook__c(Name = key, Key__c = key, Explanation__c = SPEC.get(key)[1]);
        }
        row.Value__c = JSON.serialize(value);
        upsert row;
        if (key == 'slot_times') {
            Map<String, Object> t = (Map<String, Object>) value;
            InstaAgent_Settings__c s = InstaAgentConfig.get();
            if (t.containsKey('Morning')) s.Morning_Slot_IST__c = (String) t.get('Morning');
            if (t.containsKey('Midday')) s.Midday_Slot_IST__c = (String) t.get('Midday');
            if (t.containsKey('Evening')) s.Evening_Slot_IST__c = (String) t.get('Evening');
            upsert s;
        }
    }

    @TestVisible
    static String validate(String key, Object value) {
        if (key == 'explore_share') return range(value, 0.05, 0.5);
        if (key == 'drop_after_losers') return range(value, 1, 4);
        if (key == 'drop_hours') return range(value, 12, 168);
        if (key == 'focus_shows' || key == 'avoid_shows' || key == 'hook_rules') {
            if (!(value instanceof List<Object>)) return key + ' must be a JSON list of strings.';
            Integer max = key == 'hook_rules' ? 8 : 10;
            if (((List<Object>) value).size() > max) return key + ' allows at most ' + max + ' items.';
            for (Object o : (List<Object>) value) {
                if (!(o instanceof String) || ((String) o).length() > 200) return key + ' items must be short strings.';
            }
            return null;
        }
        if (!(value instanceof Map<String, Object>)) return key + ' must be a JSON object.';
        Map<String, Object> m = (Map<String, Object>) value;
        if (key == 'format_mix') {
            Set<String> formats = new Set<String>();
            for (Schema.PicklistEntry e : Anime_Content__c.Format__c.getDescribe().getPicklistValues()) formats.add(e.getValue());
            for (String f : m.keySet()) {
                if (!formats.contains(f)) return 'unknown format "' + f + '".';
                String p = range(m.get(f), 0, 1);
                if (p != null) return f + ': ' + p;
            }
            return null;
        }
        if (key == 'slot_times') {
            List<Integer> minutes = new List<Integer>();
            for (String slot : m.keySet()) {
                if (slot != 'Morning' && slot != 'Midday' && slot != 'Evening') return 'slots are Morning, Midday and Evening.';
                String t = String.valueOf(m.get(slot));
                if (!Pattern.matches('([01][0-9]|2[0-3]):[0-5][0-9]', t)) return slot + ' must be HH:mm.';
                Integer mins = Integer.valueOf(t.left(2)) * 60 + Integer.valueOf(t.right(2));
                if (mins < 7 * 60 || mins > 23 * 60 + 30) return slot + ' must be between 07:00 and 23:30 IST.';
                minutes.add(mins);
            }
            minutes.sort();
            for (Integer i = 1; i < minutes.size(); i++) {
                if (minutes[i] - minutes[i - 1] < 180) return 'slots must be at least 3 hours apart (posting-safety).';
            }
            return null;
        }
        if (key == 'style_defaults') {
            Map<String, Object> caps = ShowrunnerEditorCapabilities.load();
            Map<String, Object> style = (Map<String, Object>) caps.get('style');
            Map<String, Object> defaults = style == null ? new Map<String, Object>() : (Map<String, Object>) style.get('defaults');
            for (String k : m.keySet()) {
                if (defaults != null && !defaults.containsKey(k)) return 'unknown style setting "' + k + '" (see get_editor_capabilities).';
            }
            return null; // values are clamped by the editor itself
        }
        return null;
    }

    static String range(Object value, Decimal lo, Decimal hi) {
        Decimal d;
        try {
            d = Decimal.valueOf(String.valueOf(value));
        } catch (Exception e) {
            return 'must be a number between ' + lo + ' and ' + hi + '.';
        }
        return d < lo || d > hi ? 'must be between ' + lo + ' and ' + hi + '.' : null;
    }

    /** Judges active changes against their baseline; rolls back the ones that made things worse. */
    public static Integer review() {
        Integer rolledBack = 0;
        List<Strategy_Change__c> changes = [SELECT Id, CreatedDate, Setting__c, Old_Value__c, New_Value__c, Baseline_Index__c, Changed_By__c
                                            FROM Strategy_Change__c WHERE Status__c = 'Active' ORDER BY CreatedDate LIMIT 20];
        for (Strategy_Change__c ch : changes) {
            List<Anime_Content__c> since = Scoreboard.scoredSince(ch.CreatedDate);
            ch.Reels_Since__c = since.size();
            ch.Result_Index__c = Scoreboard.avg(since);
            if (since.size() >= JUDGE_AFTER && ch.Baseline_Index__c != null && ch.Result_Index__c < ch.Baseline_Index__c * ROLLBACK_DROP) {
                apply(ch.Setting__c, JSON.deserializeUntyped(ch.Old_Value__c));
                ch.Status__c = 'Rolled back';
                rolledBack++;
                ShowrunnerSupport.log('Strategist', 'Rolled back change', null, ch.Setting__c + ' back to ' + ch.Old_Value__c
                    + ': results fell from ' + ch.Baseline_Index__c + ' to ' + ch.Result_Index__c + ' over ' + since.size() + ' Reels.', false, 'warning');
            } else if (since.size() >= KEEP_AFTER) {
                ch.Status__c = 'Kept';
            }
        }
        if (!changes.isEmpty()) {
            update changes;
        }
        return rolledBack;
    }
}
