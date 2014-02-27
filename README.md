zabbix2zendesk
==============

Python based Zabbix alert script that opens a Zendesk tickets when a Zabbix event occurs and closes the ticket when the event is okay.

Zendesk tickets will get collaborators based on Zabbix host groups and contacts. The idea is to automaically alert customers of outages on servers related to them and keep the dialog on the same Zendesk ticket.
