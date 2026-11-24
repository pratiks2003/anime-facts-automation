/**
 * Runs Prompt Builder templates (Einstein, GPT-4.1 via the Trust Layer) that take one
 * JSON "request" input and reply with JSON. Tests inject canned replies via `mock`.
 */
public with sharing class PromptRunner {
    @TestVisible
    private static Map<String, List<String>> mock;
    public static Integer calls = 0;

    public class PromptException extends Exception {
    }

    public static Map<String, Object> runJson(String templateName, Map<String, Object> request) {
        String text = run(templateName, JSON.serialize(request));
        return parseJson(text, templateName);
    }

    public static String run(String templateName, String requestJson) {
        calls++;
        if (Test.isRunningTest()) {
            if (mock == null || !mock.containsKey(templateName) || mock.get(templateName).isEmpty()) {
                throw new PromptException('No mock reply for ' + templateName);
            }
            return mock.get(templateName).remove(0);
        }
        ConnectApi.EinsteinPromptTemplateGenerationsInput input = new ConnectApi.EinsteinPromptTemplateGenerationsInput();
        ConnectApi.WrappedValue value = new ConnectApi.WrappedValue();
        value.value = requestJson;
        input.inputParams = new Map<String, ConnectApi.WrappedValue>{ 'Input:request' => value };
        input.additionalConfig = new ConnectApi.EinsteinLlmAdditionalConfigInput();
        input.additionalConfig.applicationName = 'PromptBuilderPreview';
        input.isPreview = false;
        ConnectApi.EinsteinPromptTemplateGenerationsRepresentation out = ConnectApi.EinsteinLLM.generateMessagesForPromptTemplate(
            templateName,
            input
        );
        if (out.generations == null || out.generations.isEmpty()) {
            throw new PromptException(templateName + ' returned no generation');
        }
        return out.generations[0].text;
    }

    /** Tolerates ```json fences and prose around the JSON object. */
    public static Map<String, Object> parseJson(String text, String source) {
        if (String.isBlank(text)) {
            throw new PromptException(source + ': empty reply');
        }
        Integer startIdx = text.indexOf('{');
        Integer endIdx = text.lastIndexOf('}');
        if (startIdx < 0 || endIdx <= startIdx) {
            throw new PromptException(source + ': reply had no JSON object');
        }
        try {
            return (Map<String, Object>) JSON.deserializeUntyped(text.substring(startIdx, endIdx + 1));
        } catch (Exception e) {
            throw new PromptException(source + ': invalid JSON (' + e.getMessage() + ')');
        }
    }
}
