/**
 * The channel's library of Reel styles (Style__c, Style Spec JSON read by video/style.py).
 * Learns from performance: Testing styles graduate to Active or get Retired after a fair trial.
 */
public with sharing class StyleLibrary {
    public static final Integer TRIAL_REELS = 3;
    public static final Decimal GRADUATE_AT = 1.0;
    public static final Decimal RETIRE_BELOW = 0.8;

    /** Seed looks so the Strategist has real choices on day one (inserted only when the library is empty). */
    public static List<Style__c> ensureSeeds() {
        List<Style__c> existing = [SELECT Id FROM Style__c LIMIT 1];
        if (!existing.isEmpty()) {
            return new List<Style__c>();
        }
        List<Style__c> seeds = new List<Style__c>{
            seed('Cinematic Story', 'Active', 'Full-bleed anime shots every ~2.6s, dark grade, glitch cuts, one-word captions, fast voice. Modelled on top story Reels.',
                new Map<String, Object>{ 'layout' => 'cinematic', 'image_interval' => 2.6, 'motion' => 0.7, 'grading' => 'dark', 'transition' => 'glitch',
                    'caption_mode' => 'word', 'voice_speed' => 1.3, 'music_gain_db' => -7, 'target_seconds' => 45 }),
            seed('Hype Montage', 'Testing', 'Rapid 1.6s cuts, vivid colour, white flashes, punchy voice. For battles, power rankings and news hype.',
                new Map<String, Object>{ 'layout' => 'cinematic', 'image_interval' => 1.6, 'motion' => 0.9, 'grading' => 'vivid', 'transition' => 'flash',
                    'caption_mode' => 'word', 'caption_size' => 128, 'voice_speed' => 1.35, 'music_gain_db' => -6, 'sfx_density' => 'high', 'target_seconds' => 35 }),
            seed('Lore Slow-Burn', 'Testing', 'Longer 3.4s shots, slow push-ins, moody grade, 2-3 word captions. For backstories and theories.',
                new Map<String, Object>{ 'layout' => 'cinematic', 'image_interval' => 3.4, 'motion' => 0.5, 'grading' => 'dark', 'transition' => 'cut',
                    'caption_mode' => 'chunk', 'caption_size' => 100, 'voice_speed' => 1.18, 'music_gain_db' => -8, 'sfx_density' => 'low', 'target_seconds' => 60 }),
            seed('Classic Cards', 'Retired', 'The original v1 look: headline cards, tags and a progress bar.',
                new Map<String, Object>{ 'layout' => 'classic', 'voice_speed' => 1.1, 'show_headline' => true, 'show_tags' => true, 'show_brand' => true,
                    'show_progress' => true, 'target_seconds' => 40 })
        };
        insert seeds;
        return seeds;
    }

    private static Style__c seed(String name, String status, String description, Map<String, Object> spec) {
        spec.put('name', name);
        return new Style__c(Name = name, Status__c = status, Origin__c = 'Seed', Description__c = description, Spec__c = JSON.serialize(spec));
    }

    /** Recompute per-style stats from published Reels (last 90 days) and graduate / retire Testing styles. */
    public static List<Style__c> refreshStats() {
        Datetime since = Datetime.now().addDays(-90);
        Map<Id, List<Social_Post__c>> byStyle = new Map<Id, List<Social_Post__c>>();
        Decimal totalViews = 0;
        Integer totalPosts = 0;
        for (Anime_Content__c c : [
            SELECT Style__c, (SELECT Views__c, Avg_Watch_Time_Ms__c, Shares__c FROM Social_Posts__r WHERE Views__c != null LIMIT 1)
            FROM Anime_Content__c
            WHERE Status__c = 'Published' AND CreatedDate >= :since
            LIMIT 500
        ]) {
            if (c.Social_Posts__r.isEmpty()) {
                continue;
            }
            Social_Post__c p = c.Social_Posts__r[0];
            totalViews += p.Views__c;
            totalPosts++;
            if (c.Style__c != null) {
                if (!byStyle.containsKey(c.Style__c)) {
                    byStyle.put(c.Style__c, new List<Social_Post__c>());
                }
                byStyle.get(c.Style__c).add(p);
            }
        }
        Decimal channelAvg = totalPosts == 0 ? 0 : totalViews / totalPosts;
        List<Style__c> styles = [SELECT Id, Name, Status__c, Origin__c FROM Style__c LIMIT 200];
        for (Style__c s : styles) {
            List<Social_Post__c> posts = byStyle.get(s.Id);
            if (posts == null) {
                continue;
            }
            Decimal views = 0, watch = 0, shares = 0;
            for (Social_Post__c p : posts) {
                views += p.Views__c;
                watch += p.Avg_Watch_Time_Ms__c == null ? 0 : p.Avg_Watch_Time_Ms__c / 1000;
                shares += p.Shares__c == null ? 0 : p.Shares__c;
            }
            Integer n = posts.size();
            s.Reels__c = n;
            s.Avg_Views__c = (views / n).round();
            s.Avg_Watch_Seconds__c = (watch / n).setScale(2);
            s.Avg_Shares__c = (shares / n).setScale(2);
            s.Performance_Score__c = channelAvg == 0 ? 1 : ((views / n) / channelAvg).setScale(2);
            if (s.Status__c == 'Testing' && n >= TRIAL_REELS) {
                s.Status__c = s.Performance_Score__c >= GRADUATE_AT ? 'Active' : (s.Performance_Score__c < RETIRE_BELOW ? 'Retired' : 'Testing');
            }
        }
        update styles;
        return styles;
    }

    /** Styles the Strategist may use (Active + Testing), with stats, newest data first. */
    public static List<Style__c> usable() {
        return [
            SELECT Id, Name, Status__c, Origin__c, Description__c, Spec__c, Reels__c, Avg_Views__c, Avg_Watch_Seconds__c,
                Avg_Shares__c, Performance_Score__c, Last_Used__c
            FROM Style__c
            WHERE Status__c IN ('Active', 'Testing')
            ORDER BY Performance_Score__c DESC NULLS LAST, Name
            LIMIT 30
        ];
    }

    public static Map<String, Object> describe(Style__c s) {
        Object spec;
        try {
            spec = JSON.deserializeUntyped(s.Spec__c);
        } catch (Exception e) {
            spec = new Map<String, Object>();
        }
        return new Map<String, Object>{
            'name' => s.Name, 'status' => s.Status__c, 'origin' => s.Origin__c, 'description' => s.Description__c, 'spec' => spec,
            'reels' => s.Reels__c, 'avg_views' => s.Avg_Views__c, 'avg_watch_seconds' => s.Avg_Watch_Seconds__c,
            'avg_shares' => s.Avg_Shares__c, 'performance' => s.Performance_Score__c
        };
    }
}
