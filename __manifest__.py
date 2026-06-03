{
    "name": "Real Estate CRM VOIP",
    "summary": "Real estate lead management with Asterisk click-to-call, call history, recordings, and commissions",
    "description": """
Real Estate CRM VOIP extends CRM with property lead fields, assigned-agent dashboards,
Asterisk AMI click-to-call, filesystem call recordings, secured playback/download,
agent performance reporting, and commission tracking.
    """,
    "author": "Zavior Tech",
    "website": "https://www.zavior.org",
    "category": "Sales/CRM",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": [
        "crm",
        "mail",
    ],
    "data": [
        "security/real_estate_security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/res_config_settings_views.xml",
        "views/res_users_views.xml",
        "views/realestate_call_views.xml",
        "views/agent_commission_views.xml",
        "views/agent_performance_views.xml",
        "views/crm_lead_views.xml",
        "views/menu_views.xml",
    ],
    "application": True,
    "installable": True,
}
