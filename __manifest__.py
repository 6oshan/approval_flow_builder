{
    'name': 'Approval Flow Builder',
    'version': '19.0.1.2.0',
    'category': 'Purchases',
    'author': 'Roshan',
    'license': 'LGPL-3',
    'summary': 'Visual approval flow builder for Purchase Orders',
    'description': """
        Visual approval flow builder for Purchase Orders.
    """,
    'depends': [
        'mail',
        'purchase',
        'hr',
    ],
    'data': [
        'security/approval_flow_category.xml',
        'security/approval_flow_security.xml',
        'security/ir.model.access.csv',
        'data/approval_flow_data.xml',
        'views/approval_flow_views.xml',
        'views/approval_flow_request_views.xml',
        'views/purchase_order_views.xml',
        'views/approval_flow_log_views.xml',
        'views/res_config_settings_views.xml',
        'wizard/approval_reject_wizard_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'approval_flow_builder/static/src/flow_builder/flow_builder.scss',
            'approval_flow_builder/static/src/flow_builder/flow_builder.js',
            'approval_flow_builder/static/src/flow_builder/flow_builder.xml',
        ],
    },
    'installable': True,
    'application': True,
}
