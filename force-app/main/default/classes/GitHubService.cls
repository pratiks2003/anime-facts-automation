/**
 * GitHub Actions workflow dispatch through the GitHub_API Named Credential
 * (the token lives in the GitHub External Credential, never in code).
 */
public with sharing class GitHubService {
    public static final String NAMED_CREDENTIAL = 'callout:GitHub_API';

    public class GitHubException extends Exception {
    }

    /**
     * POST /repos/{repo}/actions/workflows/{file}/dispatches. GitHub answers 204 with no
     * body; the run is matched later through the job_id input (run-name + callback).
     */
    public static void dispatchWorkflow(String repository, String workflowFile, Map<String, String> inputs) {
        if (String.isBlank(repository) || String.isBlank(workflowFile)) {
            throw new GitHubException('Repository and workflow file are required');
        }
        HttpRequest req = new HttpRequest();
        req.setEndpoint(NAMED_CREDENTIAL + '/repos/' + repository + '/actions/workflows/' + workflowFile + '/dispatches');
        req.setMethod('POST');
        req.setHeader('Accept', 'application/vnd.github+json');
        req.setHeader('X-GitHub-Api-Version', '2022-11-28');
        req.setHeader('Content-Type', 'application/json');
        req.setTimeout(30000);
        req.setBody(JSON.serialize(new Map<String, Object>{ 'ref' => 'main', 'inputs' => inputs }));

        HttpResponse res = new Http().send(req);
        if (res.getStatusCode() != 204 && res.getStatusCode() != 200) {
            String body = res.getBody() == null ? '' : res.getBody().abbreviate(500);
            throw new GitHubException('GitHub dispatch failed: HTTP ' + res.getStatusCode() + ' ' + body);
        }
    }
}
