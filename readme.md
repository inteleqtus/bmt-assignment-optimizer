# BMT Assignment Optimizer Deployment

## Step 1: Deploy to Railway.app (15 minutes)

### Quick Setup
1. **Create Railway account**: Go to railway.app and sign up with GitHub
2. **Create new project**: Click "New Project" → "Deploy from GitHub repo"
3. **Upload these files to a GitHub repo**:
   - `app.py` (main Flask application)
   - `requirements.txt` (Python dependencies)
   - `README.md` (this file)

### Railway Configuration
4. **Connect GitHub repo**: Select your repository in Railway
5. **Set environment variables**: 
   - `PORT` = 5000 (Railway sets this automatically)
6. **Deploy**: Railway will automatically build and deploy

### Test Deployment
7. **Get your Railway URL**: Something like `https://your-app-name.railway.app`
8. **Test endpoints**:
   - Health check: `GET https://your-app.railway.app/`
   - Sample test: `GET https://your-app.railway.app/test`

## Step 2: Test with Postman/curl (5 minutes)

### Test Health Check
```bash
curl https://your-app.railway.app/
```
Should return:
```json
{
  "status": "healthy",
  "service": "BMT Assignment Optimizer",
  "version": "1.0.0"
}
```

### Test Sample Optimization
```bash
curl https://your-app.railway.app/test
```
Should return assignment results with nurses and patients.

### Test Custom Data
```bash
curl -X POST https://your-app.railway.app/optimize \
  -H "Content-Type: application/json" \
  -d '{
    "nurses": [
      {"Nurse_ID": "N001", "Name": "Test Nurse", "Skill_Level": 2, "Chemo_IV_Cert": "Y", "Max_Patients": 4}
    ],
    "patients": [
      {"Patient_ID": "101", "Initials": "A.B.", "Acuity": 5, "Chemo_Type": "oral"}
    ]
  }'
```

## Step 3: Set up n8n Integration (20 minutes)

### Create n8n Account
1. Go to n8n.cloud and create account
2. Create new workflow: "BMT_Daily_Assignments"

### Workflow Nodes

**Node 1: Schedule Trigger**
- Service: Schedule Trigger
- Rule: 0 30 18 * * * (6:30 PM daily)
- Timezone: America/Chicago

**Node 2: HTTP Request (Test)**
- Method: GET
- URL: `https://your-app.railway.app/test`
- Headers: Content-Type: application/json

**Node 3: SMS Notification (Later)**
- Split assignments array
- Send SMS per nurse via Twilio

### Test n8n Workflow
1. Save workflow
2. Execute manually
3. Check execution log for successful API call
4. Verify assignment data received

## Step 4: Monitor and Verify (10 minutes)

### Railway Dashboard Monitoring
- **Deployments**: Check build and deploy status
- **Metrics**: Monitor response times and errors
- **Logs**: View real-time application logs
- **Settings**: Manage environment variables

### Key Metrics to Watch
- Response time for `/optimize` endpoint (~1-3 seconds)
- Memory usage (should stay under 512MB)
- Daily execution at 18:30
- Error rates (should be 0%)

### Troubleshooting Common Issues

**Build Fails**:
- Check requirements.txt for correct versions
- Verify all files are in root directory
- Check Railway build logs

**API Returns Errors**:
- Test `/` endpoint first (health check)
- Check logs in Railway dashboard
- Verify JSON format in requests

**Optimization Takes Too Long**:
- Check patient count (<20)
- Verify constraint feasibility
- Review solver timeout (30 seconds)

## Step 5: Next Steps (Production Ready)

### Phase 2: Add Google Sheets Integration
- Connect n8n to your Google Sheets
- Replace `/test` with real data endpoint
- Add data validation and error handling

### Phase 3: Add SMS Notifications
- Set up Twilio account
- Configure phone numbers in nurse data
- Add SMS sending nodes in n8n

### Phase 4: Migration to Heroku
- Create Heroku app
- Transfer code and dependencies
- Set up monitoring and alerts
- Update n8n webhook URLs

## Emergency Fallback Plan

If Railway service goes down:
1. Check Railway status page
2. Use manual assignment process
3. Alert IT/management
4. Consider immediate Heroku migration

## Support Contacts
- Railway Support: help@railway.app
- n8n Support: Via in-app chat
- Emergency: [Your IT contact]

---

**Service URLs (Update with your actual URLs):**
- Railway App: `https://your-app.railway.app`
- n8n Workflow: `https://app.n8n.cloud/workflow/[id]`
- Google Sheet: `https://docs.google.com/spreadsheets/d/1F7efHKYcApmc-uZ1z_GBfMipGo8Ob-N-dBrlGtASwwo`