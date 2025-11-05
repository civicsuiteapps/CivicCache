# API Key Setup Guide

This guide walks you through obtaining API keys for all data sources used by CivicCache.

## Quick Start

1. Copy the `.env` file and fill in your API keys:
   ```bash
   # The .env file is already created in the project root
   # Simply open it and add your keys
   ```

2. Start with the essential APIs (all free):
   - Congress.gov
   - OpenFEC
   - US Census Bureau

3. Add optional APIs as needed for additional features

---

## Essential APIs (Free)

### 1. Congress.gov API ⭐ HIGH PRIORITY

**Purpose**: Legislative data, bills, votes, committees

**Sign Up**: https://api.congress.gov/sign-up/

**Steps**:
1. Visit the sign-up page
2. Fill out the form with your name and email
3. Agree to the terms of service
4. Check your email for the API key
5. Add to `.env`: `CONGRESS_GOV_API_KEY=your_key_here`

**Rate Limit**: 5,000 requests per hour

**Test Your Key**:
```bash
curl "https://api.congress.gov/v3/bill/119?api_key=YOUR_KEY&limit=1"
```

---

### 2. OpenFEC API ⭐ HIGH PRIORITY

**Purpose**: Real-time campaign finance data

**Sign Up**: https://api.open.fec.gov/developers/

**Steps**:
1. Click "Get API Key"
2. Enter your email and organization (can use personal)
3. Check your email for the API key (instant)
4. Add to `.env`: `OPENFEC_API_KEY=your_key_here`

**Rate Limit**: 1,000 requests per hour (120 per minute)

**Test Your Key**:
```bash
curl "https://api.open.fec.gov/v1/candidates/?api_key=YOUR_KEY&per_page=1"
```

---

### 3. US Census Bureau API

**Purpose**: Demographics, economic data, district boundaries

**Sign Up**: https://api.census.gov/data/key_signup.html

**Steps**:
1. Fill out the request form
2. Provide name, email, and organization
3. Check email for API key activation link
4. Click the activation link
5. Check email again for your activated key
6. Add to `.env`: `CENSUS_API_KEY=your_key_here`

**Rate Limit**: No strict limit (be reasonable)

**Test Your Key**:
```bash
curl "https://api.census.gov/data/2021/acs/acs5?get=NAME,B01001_001E&for=state:*&key=YOUR_KEY"
```

---

### 4. ProPublica Congress API ⭐ RECOMMENDED

**Purpose**: Enhanced congressional data, voting records, statements

**Sign Up**: https://www.propublica.org/datastore/api/propublica-congress-api

**Steps**:
1. Click "Request an API Key"
2. Fill out the form explaining your use case
3. Mention it's for civic engagement/transparency
4. Wait for approval email (usually within 24 hours)
5. Add to `.env`: `PROPUBLICA_API_KEY=your_key_here`

**Rate Limit**: 5,000 requests per day

**Test Your Key**:
```bash
curl "https://api.propublica.org/congress/v1/117/senate/members.json" \
  -H "X-API-Key: YOUR_KEY"
```

---

## Optional APIs

### 5. OpenSecrets API (Center for Responsive Politics)

**Purpose**: Lobbying, industry contributions, dark money

**Sign Up**: https://www.opensecrets.org/open-data/api

**Important**:
- Requires donation/subscription to OpenSecrets
- Suggested donation: $50+ for API access
- Worth it for lobbying and influence tracking

**Steps**:
1. Make a donation to OpenSecrets
2. Request API access via their contact form
3. Wait for manual approval and key delivery
4. Add to `.env`: `OPENSECRETS_API_KEY=your_key_here`

**Alternative**: Use bulk data downloads instead (free)
- https://www.opensecrets.org/open-data/bulk-data

---

### 6. Google Civic Information API

**Purpose**: Find representatives by address, election info

**Sign Up**: https://console.cloud.google.com/

**Steps**:
1. Create a Google Cloud Platform account (free tier)
2. Create a new project
3. Enable the "Google Civic Information API"
4. Go to "Credentials" and create an API key
5. Restrict the key to Civic Information API only (security)
6. Add to `.env`: `GOOGLE_CIVIC_API_KEY=your_key_here`

**Rate Limit**: 25,000 requests per day (free tier)

**Test Your Key**:
```bash
curl "https://www.googleapis.com/civicinfo/v2/representatives?address=1600%20Pennsylvania%20Ave%20NW&key=YOUR_KEY"
```

---

### 7. Twitter API (X)

**Purpose**: Track congressional member tweets and engagement

**Sign Up**: https://developer.twitter.com/en/portal/dashboard

**Important**:
- Free tier is very limited (500k tweets/month)
- Basic tier: $100/month for better access
- Pro tier: $5,000/month for full access
- Consider if social media tracking is essential

**Steps**:
1. Create a Twitter Developer account
2. Apply for API access (explain your use case)
3. Wait for approval (can take days)
4. Create an app in the developer portal
5. Generate API keys and tokens
6. Add to `.env`:
   ```
   TWITTER_API_KEY=your_key
   TWITTER_API_SECRET=your_secret
   TWITTER_BEARER_TOKEN=your_token
   ```

---

### 8. LegiScan API

**Purpose**: State-level legislation tracking

**Sign Up**: https://legiscan.com/legiscan

**Steps**:
1. Create an account on LegiScan
2. Request API access (explain use case)
3. Choose a subscription tier:
   - Free tier: Limited access
   - Paid tiers: $50-$500/month
4. Add to `.env`: `LEGISCAN_API_KEY=your_key_here`

**Note**: Only needed if you want state-level bill tracking

---

## Recommended Setup Order

### Phase 1: Core Data (Start Here)
1. ✅ **Congress.gov API** - Bills, votes, committees
2. ✅ **OpenFEC API** - Campaign finance
3. ✅ **Census API** - Demographics

**Time**: ~30 minutes
**Cost**: FREE
**Result**: Full congressional and campaign finance data

### Phase 2: Enhanced Features
4. ✅ **ProPublica API** - Enhanced member data
5. ⚠️ **OpenSecrets API** - Lobbying (requires donation)

**Time**: 1-2 days (waiting for approvals)
**Cost**: $50 donation recommended
**Result**: Lobbying and industry influence tracking

### Phase 3: Advanced Features (Optional)
6. **Google Civic API** - Address-based lookup
7. **Twitter API** - Social media tracking
8. **LegiScan API** - State legislation

**Time**: Varies
**Cost**: $100-$5,000/month (Twitter, LegiScan)
**Result**: Social media and state-level tracking

---

## Security Best Practices

### 1. Environment Variables
- ✅ API keys stored in `.env` file
- ✅ `.env` is in `.gitignore`
- ✅ Never commit keys to git
- ✅ Use `.env.example` for templates

### 2. Key Rotation
- Rotate keys every 90 days
- Use different keys for dev/staging/production
- Revoke keys immediately if exposed

### 3. Access Control
- Restrict API keys to specific IPs (when available)
- Use API key restrictions in Google Cloud Console
- Monitor API usage for unusual activity

### 4. Production Secrets
Consider using a secrets manager for production:
- AWS Secrets Manager
- HashiCorp Vault
- Google Secret Manager
- Azure Key Vault

---

## Troubleshooting

### "Invalid API Key" Error
- Check that you've activated the key (Census requires activation)
- Ensure no extra spaces in `.env` file
- Verify key is correctly copied (some have dashes)

### "Rate Limit Exceeded"
- Check your usage in the API dashboard
- Implement caching to reduce requests
- Consider upgrading to paid tier if needed

### "Access Denied"
- Some APIs require approval (ProPublica, Twitter)
- Check your email for approval status
- Reapply if denied, explaining civic engagement purpose

### Key Not Working
1. Test the key with curl (examples above)
2. Check API status page for outages
3. Verify your account is active
4. Regenerate the key if necessary

---

## API Usage Examples

Once you have your keys set up, test them:

```bash
# Load environment variables
source .env

# Test Congress.gov
curl "https://api.congress.gov/v3/bill/119?api_key=$CONGRESS_GOV_API_KEY&limit=1"

# Test OpenFEC
curl "https://api.open.fec.gov/v1/candidates/?api_key=$OPENFEC_API_KEY&per_page=1"

# Test Census
curl "https://api.census.gov/data/2021/acs/acs5?get=NAME&for=state:*&key=$CENSUS_API_KEY"

# Test ProPublica
curl "https://api.propublica.org/congress/v1/119/senate/members.json" \
  -H "X-API-Key: $PROPUBLICA_API_KEY"
```

---

## Cost Summary

| API | Cost | Rate Limit | Priority |
|-----|------|------------|----------|
| Congress.gov | FREE | 5,000/hour | ⭐⭐⭐ Essential |
| OpenFEC | FREE | 1,000/hour | ⭐⭐⭐ Essential |
| Census | FREE | Unlimited* | ⭐⭐⭐ Essential |
| ProPublica | FREE | 5,000/day | ⭐⭐ Recommended |
| OpenSecrets | $50+ donation | Varies | ⭐⭐ Recommended |
| Google Civic | FREE | 25,000/day | ⭐ Optional |
| Twitter | $100-5,000/mo | Varies | ⭐ Optional |
| LegiScan | $50-500/mo | Varies | ⭐ Optional |

**Total cost to get started**: $0 (with 3 essential free APIs)

**Recommended investment**: $50 for OpenSecrets (one-time donation)

---

## Next Steps

After setting up your API keys:

1. **Test the APIs** using the curl examples above
2. **Run the database build script** with live API data
3. **Implement API integration scripts** (see `docs/ADDITIONAL_DATA_SOURCES.md`)
4. **Set up caching** to respect rate limits
5. **Monitor usage** in each API's dashboard

---

## Support

If you have issues with any API:

- **Congress.gov**: email api@loc.gov
- **OpenFEC**: https://github.com/fecgov/openFEC/issues
- **Census**: https://www.census.gov/data/developers/about/contact-us.html
- **ProPublica**: https://www.propublica.org/contact
- **OpenSecrets**: https://www.opensecrets.org/about/contact.php

For CivicCache-specific issues, open an issue on GitHub.
