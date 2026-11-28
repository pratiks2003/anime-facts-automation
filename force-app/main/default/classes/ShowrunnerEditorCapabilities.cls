/**
 * Showrunner action: everything the video editor can do (effects per scene role, style settings and their
 * limits, what is always on, what it cannot do yet). Generated from the Python editor (video/editing.py)
 * into the EditorCapabilities static resource, so the agents always see the real capabilities.
 */
public with sharing class ShowrunnerEditorCapabilities {
    public class Request {
        @InvocableVariable(label='Topic' description='Optional: effects, style or all (default all)')
        public String topic;
    }
    public class Result {
        @InvocableVariable(label='Capabilities' description='The editor capability manifest as JSON')
        public String capabilitiesJson;
    }

    @InvocableMethod(label='Get Editor Capabilities' description='What the video editor can do: per-scene effects, style settings with limits, and what it cannot do yet.')
    public static List<Result> run(List<Request> requests) {
        Result r = new Result();
        String topic = requests.isEmpty() || requests[0].topic == null ? 'all' : requests[0].topic.trim().toLowerCase();
        Map<String, Object> all = load();
        if (topic == 'effects' || topic == 'style') {
            Map<String, Object> part = new Map<String, Object>{ 'how_to_request' => all.get('how_to_request') };
            part.put(topic, all.get(topic));
            if (topic == 'effects') part.put('effect_colors', all.get('effect_colors'));
            r.capabilitiesJson = JSON.serialize(part);
        } else {
            r.capabilitiesJson = JSON.serialize(all);
        }
        return new List<Result>{ r };
    }

    public static Map<String, Object> load() {
        List<StaticResource> rows = [SELECT Body FROM StaticResource WHERE Name = 'EditorCapabilities' LIMIT 1];
        if (rows.isEmpty()) {
            return new Map<String, Object>{ 'error' => 'capability manifest missing' };
        }
        return (Map<String, Object>) JSON.deserializeUntyped(rows[0].Body.toString());
    }

    /** Adds the agent's editing requests (JSON text) to a directive; bad JSON is kept as text for the feature log. */
    public static void addEditing(Map<String, Object> directive, String style, String edits) {
        if (String.isNotBlank(style)) directive.put('style', parseOrText(style));
        if (String.isNotBlank(edits)) directive.put('edits', parseOrText(edits));
    }

    private static Object parseOrText(String value) {
        try {
            return JSON.deserializeUntyped(value);
        } catch (Exception e) {
            return value;
        }
    }
}