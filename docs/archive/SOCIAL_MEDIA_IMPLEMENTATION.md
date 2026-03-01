# Implementing Social Media Fields in Member Detail Page

This document describes how to implement the social media and contact form fields in the CivicScope iOS app's member detail page.

## Database Fields

The `congress_members` table now includes these social media and contact fields:

| Field | Type | Description | Coverage |
|-------|------|-------------|----------|
| `twitter_handle` | TEXT | Twitter/X username (without @) | 95% |
| `facebook_url` | TEXT | Full Facebook profile URL | 92% |
| `youtube_url` | TEXT | Full YouTube channel URL | 48% |
| `contact_form_url` | TEXT | URL to official contact form | 95% |

## Data Model

### Update CongressMember Model

Add the new fields to your SwiftData or struct model:

```swift
import SwiftData

@Model
class CongressMember {
    @Attribute(.unique) var id: String  // Bioguide ID
    var firstName: String
    var lastName: String
    var fullName: String
    var chamber: String
    var stateCode: String
    var currentParty: String

    // Contact Information
    var phone: String?
    var officeAddress: String?
    var websiteURL: String?
    var contactFormURL: String?       // NEW

    // Social Media
    var twitterHandle: String?         // NEW
    var facebookURL: String?           // NEW
    var youtubeURL: String?            // NEW

    // ... other fields
}
```

### SQLite Column Mapping

When querying the database, map columns as follows:

```swift
struct CongressMemberDTO: Codable {
    let id: String
    let firstName: String
    let lastName: String
    let fullName: String

    // Social media (snake_case from SQLite)
    let twitterHandle: String?
    let facebookUrl: String?
    let youtubeUrl: String?
    let contactFormUrl: String?

    enum CodingKeys: String, CodingKey {
        case id
        case firstName = "first_name"
        case lastName = "last_name"
        case fullName = "full_name"
        case twitterHandle = "twitter_handle"
        case facebookUrl = "facebook_url"
        case youtubeUrl = "youtube_url"
        case contactFormUrl = "contact_form_url"
    }
}
```

## UI Implementation

### Social Media Button Row

Create a reusable component for social media links:

```swift
import SwiftUI

struct SocialMediaRow: View {
    let member: CongressMember
    @Environment(\.openURL) private var openURL

    var body: some View {
        HStack(spacing: 16) {
            if let twitter = member.twitterHandle {
                SocialButton(
                    icon: "bird",  // or custom Twitter/X icon
                    label: "Twitter",
                    color: .black
                ) {
                    openURL(URL(string: "https://twitter.com/\(twitter)")!)
                }
            }

            if let facebook = member.facebookURL,
               let url = URL(string: facebook) {
                SocialButton(
                    icon: "f.square.fill",
                    label: "Facebook",
                    color: Color(red: 0.23, green: 0.35, blue: 0.60)
                ) {
                    openURL(url)
                }
            }

            if let youtube = member.youtubeURL,
               let url = URL(string: youtube) {
                SocialButton(
                    icon: "play.rectangle.fill",
                    label: "YouTube",
                    color: .red
                ) {
                    openURL(url)
                }
            }
        }
        .padding(.vertical, 8)
    }
}

struct SocialButton: View {
    let icon: String
    let label: String
    let color: Color
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: 4) {
                Image(systemName: icon)
                    .font(.title2)
                    .foregroundStyle(color)
                Text(label)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            .frame(minWidth: 60)
        }
        .buttonStyle(.plain)
    }
}
```

### Contact Form Button

Add a prominent contact button that uses the contact form URL:

```swift
struct ContactMemberButton: View {
    let member: CongressMember
    @Environment(\.openURL) private var openURL

    var body: some View {
        if let contactURL = member.contactFormURL,
           let url = URL(string: contactURL) {
            Button {
                openURL(url)
            } label: {
                Label("Contact \(member.lastName)", systemImage: "envelope.fill")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
        } else if let phone = member.phone {
            // Fallback to phone if no contact form
            Button {
                openURL(URL(string: "tel:\(phone.filter { $0.isNumber })")!)
            } label: {
                Label("Call Office", systemImage: "phone.fill")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .controlSize(.large)
        }
    }
}
```

### Member Detail Section

Integrate into your member detail view:

```swift
struct MemberDetailView: View {
    let member: CongressMember

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                // Header with photo, name, party
                MemberHeaderView(member: member)

                // Primary action - Contact
                ContactMemberButton(member: member)
                    .padding(.horizontal)

                // Social Media Links
                if hasSocialMedia {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Connect")
                            .font(.headline)
                            .padding(.horizontal)

                        SocialMediaRow(member: member)
                            .padding(.horizontal)
                    }
                }

                Divider()

                // Other sections...
                MemberBioSection(member: member)
                MemberCommitteesSection(member: member)
                MemberLegislationSection(member: member)
            }
        }
        .navigationTitle(member.fullName)
        .navigationBarTitleDisplayMode(.inline)
    }

    private var hasSocialMedia: Bool {
        member.twitterHandle != nil ||
        member.facebookURL != nil ||
        member.youtubeURL != nil
    }
}
```

## URL Handling Best Practices

### 1. Validate URLs Before Opening

```swift
extension String {
    var isValidURL: Bool {
        guard let url = URL(string: self) else { return false }
        return UIApplication.shared.canOpenURL(url)
    }
}
```

### 2. Handle Deep Links for Native Apps

Open Twitter/X in the native app if installed:

```swift
func openTwitterProfile(_ handle: String) {
    let appURL = URL(string: "twitter://user?screen_name=\(handle)")!
    let webURL = URL(string: "https://twitter.com/\(handle)")!

    if UIApplication.shared.canOpenURL(appURL) {
        UIApplication.shared.open(appURL)
    } else {
        UIApplication.shared.open(webURL)
    }
}

func openFacebookProfile(_ urlString: String) {
    guard let url = URL(string: urlString) else { return }

    // Extract Facebook ID/username from URL for deep link
    let pathComponents = url.pathComponents
    if let username = pathComponents.last, !username.isEmpty {
        let appURL = URL(string: "fb://profile/\(username)")!
        if UIApplication.shared.canOpenURL(appURL) {
            UIApplication.shared.open(appURL)
            return
        }
    }

    UIApplication.shared.open(url)
}
```

### 3. Add URL Schemes to Info.plist

To check if apps are installed, add to `Info.plist`:

```xml
<key>LSApplicationQueriesSchemes</key>
<array>
    <string>twitter</string>
    <string>fb</string>
    <string>youtube</string>
</array>
```

## Accessibility

Ensure all social links are accessible:

```swift
SocialButton(icon: "bird", label: "Twitter", color: .black) {
    openTwitterProfile(handle)
}
.accessibilityLabel("Follow \(member.fullName) on Twitter")
.accessibilityHint("Opens Twitter profile in browser or app")
```

## Analytics Events

Track social media engagement:

```swift
enum AnalyticsEvent {
    case socialMediaTapped(memberId: String, platform: String)
    case contactFormOpened(memberId: String)
}

// In your button actions:
Analytics.log(.socialMediaTapped(memberId: member.id, platform: "twitter"))
```

## Testing

### Sample Query to Verify Data

```sql
-- Members with complete social media profiles
SELECT full_name, twitter_handle, facebook_url, youtube_url, contact_form_url
FROM congress_members
WHERE twitter_handle IS NOT NULL
  AND facebook_url IS NOT NULL
  AND contact_form_url IS NOT NULL
LIMIT 10;

-- Coverage statistics
SELECT
    COUNT(*) as total,
    SUM(CASE WHEN twitter_handle IS NOT NULL THEN 1 ELSE 0 END) as twitter,
    SUM(CASE WHEN facebook_url IS NOT NULL THEN 1 ELSE 0 END) as facebook,
    SUM(CASE WHEN youtube_url IS NOT NULL THEN 1 ELSE 0 END) as youtube,
    SUM(CASE WHEN contact_form_url IS NOT NULL THEN 1 ELSE 0 END) as contact_form
FROM congress_members;
```

### UI Test Cases

1. **Member with all social links**: Verify all 3 buttons appear
2. **Member with no social links**: Verify section is hidden
3. **Member with partial links**: Verify only available buttons appear
4. **Contact form available**: Verify primary contact button appears
5. **No contact form, has phone**: Verify fallback to phone button
6. **Deep link to Twitter app**: Verify opens in Twitter if installed
7. **VoiceOver navigation**: Verify all buttons are accessible

## Migration Notes

If updating an existing app:

1. **Database Migration**: The SQLite schema already includes these columns. No migration needed if using the latest `civic_cache.db`.

2. **Model Update**: Add optional properties to your existing model. Existing code won't break since fields are optional.

3. **Backward Compatibility**: The UI gracefully hides sections when data is unavailable, so older cached databases will still work.

## Related Files

- Database schema: `docs/DATABASE.md`
- Import script: `scripts/import_congress_legislators.py`
- Contact form discovery: `scripts/fill_contact_forms.py`
