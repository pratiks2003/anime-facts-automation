@IsTest
public class InstaAgentTestUtil {
    public static InstaAgent_Settings__c settings(Boolean enabled, Boolean twoPerDay) {
        InstaAgent_Settings__c s = InstaAgent_Settings__c.getOrgDefaults();
        if (s == null || s.Id == null) {
            s = new InstaAgent_Settings__c(SetupOwnerId = UserInfo.getOrganizationId());
        }
        s.Automation_Enabled__c = enabled;
        s.Two_Reels_Per_Day__c = twoPerDay;
        s.Auto_Approve__c = true;
        s.Dry_Run__c = true;
        s.Morning_Slot_IST__c = '09:00';
        s.Evening_Slot_IST__c = '19:00';
        s.GitHub_Repository__c = 'owner/repo';
        s.Generate_Workflow__c = 'reel.yml';
        upsert s;
        InstaAgentConfig.cached = null;
        return s;
    }

    /** A System Administrator with the InstaAgent_Admin permission set (what the site user has). */
    public static User adminUser() {
        Profile p = [SELECT Id FROM Profile WHERE Name = 'System Administrator' LIMIT 1];
        String unique = String.valueOf(Crypto.getRandomInteger()).replace('-', '');
        User u = new User(
            Alias = 'iaadm',
            Email = 'instaagent.admin@example.com',
            EmailEncodingKey = 'UTF-8',
            LastName = 'InstaAgentAdmin',
            LanguageLocaleKey = 'en_US',
            LocaleSidKey = 'en_US',
            ProfileId = p.Id,
            TimeZoneSidKey = 'Asia/Kolkata',
            Username = 'instaagent.admin.' + unique + '@example.com'
        );
        insert u;
        PermissionSet ps = [SELECT Id FROM PermissionSet WHERE Name = 'InstaAgent_Admin'];
        insert new PermissionSetAssignment(AssigneeId = u.Id, PermissionSetId = ps.Id);
        return u;
    }

    /** 09:00 IST on 2026-11-25 = 03:30 GMT. */
    public static Datetime morningSlotGmt() {
        return Datetime.newInstanceGmt(2026, 11, 25, 3, 30, 0);
    }

    public class GitHubMock implements HttpCalloutMock {
        public Integer status;
        public List<HttpRequest> requests = new List<HttpRequest>();
        public GitHubMock(Integer status) {
            this.status = status;
        }
        public HttpResponse respond(HttpRequest req) {
            requests.add(req);
            HttpResponse res = new HttpResponse();
            res.setStatusCode(status);
            res.setBody(status == 204 ? '' : '{"message":"Bad credentials"}');
            return res;
        }
    }
}
