/**
 * Starts GitHub workflows for Reels (async, because callouts can't run in triggers or
 * after uncommitted DML). Callouts happen first; all DML is done afterwards.
 */
public with sharing class ReelDispatchQueueable implements Queueable, Database.AllowsCallouts {
    public static final String GENERATE = 'Generate';
    public static final String PUBLISH = 'Publish';
    public static final Map<String, String> WORKFLOWS = new Map<String, String>{ PUBLISH => 'publish.yml' };

    private final List<Id> contentIds;
    private final String jobType;

    public ReelDispatchQueueable(List<Id> contentIds, String jobType) {
        this.contentIds = contentIds;
        this.jobType = jobType;
    }

    public void execute(QueueableContext context) {
        run(contentIds, jobType);
    }

    public static void run(List<Id> contentIds, String jobType) {
        InstaAgent_Settings__c settings = InstaAgentConfig.get();
        String workflow = jobType == GENERATE ? settings.Generate_Workflow__c : WORKFLOWS.get(jobType);
        List<Anime_Content__c> contents = [
            SELECT Id, External_Id__c, Format__c, Slot__c, Topic__c, Anime__r.Name, Scheduled_For__c, Dry_Run__c, Attempts__c
            FROM Anime_Content__c
            WHERE Id IN :contentIds
        ];
        List<Video_Job__c> jobs = new List<Video_Job__c>();
        for (Anime_Content__c c : contents) {
            Integer attempt = (c.Attempts__c == null ? 0 : c.Attempts__c.intValue()) + 1;
            String correlation = c.External_Id__c + '-' + jobType.left(3).toUpperCase() + '-' + attempt + '-' + String.valueOf(Datetime.now().getTime()).right(5);
            Video_Job__c job = new Video_Job__c(
                Content__c = c.Id,
                Job_Type__c = jobType,
                Correlation_Id__c = correlation,
                Dispatched_At__c = Datetime.now()
            );
            Map<String, String> inputs = new Map<String, String>{
                'content_id' => c.External_Id__c,
                'job_id' => correlation,
                'dry_run' => String.valueOf(c.Dry_Run__c)
            };
            if (jobType == GENERATE) {
                inputs.put('format', c.Format__c);
                inputs.put('slot', c.Slot__c == null ? 'Manual' : c.Slot__c);
                inputs.put('anime', c.Anime__r == null ? '' : c.Anime__r.Name);
                inputs.put('topic', c.Topic__c == null ? '' : c.Topic__c);
                inputs.put('auto_approve', String.valueOf(settings.Auto_Approve__c));
            }
            try {
                GitHubService.dispatchWorkflow(settings.GitHub_Repository__c, workflow, inputs);
                job.Status__c = 'Dispatched';
                c.Status__c = jobType == GENERATE ? 'Generating' : 'Publishing';
                c.Error_Message__c = null;
            } catch (Exception e) {
                job.Status__c = 'Failed';
                job.Error_Message__c = e.getMessage();
                job.Completed_At__c = Datetime.now();
                c.Status__c = 'Failed';
                c.Error_Message__c = e.getMessage();
            }
            c.Attempts__c = attempt;
            jobs.add(job);
        }
        insert jobs;
        update contents;
    }
}
