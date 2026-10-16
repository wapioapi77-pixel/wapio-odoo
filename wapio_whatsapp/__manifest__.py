{
    'name': 'Wapio WhatsApp',
    'version': '19.0.1.0.1',
    'summary': 'WhatsApp support and transactional messages from contacts, CRM, sales and invoices',
    'author': 'Wapio',
    'website': 'https://www.wapio.io',
    'support': 'contact@wapio.io',
    'images': ['static/description/queue.png'],
    'description': 'Free connector requiring a separately purchased Wapio service plan. '
                   'With sender consent, recipient numbers, message text and selected PDFs '
                   'are transmitted to Wapio for WhatsApp delivery.',
    'category': 'Discuss',
    'license': 'LGPL-3',
    'depends': ['crm', 'sale', 'account'],
    'external_dependencies': {'python': ['requests']},
    'data': ['security/security.xml', 'security/ir.model.access.csv',
             'views/connector.xml', 'views/compose.xml', 'views/source_actions.xml',
             'data/cron.xml'],
    'application': True,
    'installable': True,
    'price': 0.0,
    'currency': 'USD',
}
