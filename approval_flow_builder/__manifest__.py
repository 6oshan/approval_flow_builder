{
    'name': 'Approval Flow Builder',
    'version': '19.0.1.2.2',
    'category': 'Purchases',
    'author': 'Roshan Jose Kurian',
    'website': 'https://github.com/6oshan/approval_flow_builder',
    'maintainer': 'Roshan Jose Kurian',
    'license': 'LGPL-3',
    'summary': 'Multi-level purchase order approval with a drag-and-drop visual builder, amount rules, order locking and audit log',
    'description': """
        Multi-level Purchase Order approval workflow for Odoo 19:
        drag-and-drop approval chains, employee or group approvers,
        amount-based steps, minimum approval amount, order locking,
        approved-amount protection, reject with reason and audit log.
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
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': True,
}
