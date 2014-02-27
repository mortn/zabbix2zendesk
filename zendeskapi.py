#!/usr/bin/env python
# -*- coding: utf-8 -*-

 
import json
import logging as log
import MySQLdb
import yaml
from sys import argv
from time import time
from os import path, remove
from zendesk import Zendesk, get_id_from_url
from inspect import currentframe
import collections

log.basicConfig(level=log.DEBUG, filename='/var/log/zabbix/zendesk.log', 
	format='%(asctime)s %(levelname)s %(message)s')

def lino(): return currentframe().f_back.f_lineno

class z2z:
	def __init__(self):
		if not argv[1].startswith('support@'):
			log.debug('wrong argument 1: %s' % argv[1] )
			exit(2)
		self.zabbix_conf = '/etc/zabbix/zabbix_server.conf'
		try:
			# Input data mangling
			self.ydata = yaml.load(argv[3])
			log.debug(argv[3])
			event_id = argv[2].split(':')[0]
			event_status = argv[2].split(':')[1].strip()
			log.debug('event_id:%s, event_status:%s' % (event_id,event_status))
			# Establish MySQL connection
			self.mysql_setup()
			# Zendesk setup
			self.mycsr.execute("SELECT lower(macro),value FROM globalmacro WHERE macro like '%ZENDESK%';")
			self.zdp = dict([ (macro[10:-1], value) for (macro,value) in self.mycsr.fetchall() ])
			self.zd = Zendesk(self.zdp['url'], self.zdp['email'], self.zdp['token'], api_version=2,
				use_api_token=True, client_args={ "disable_ssl_certificate_validation": True })
			self.zd_user = self.get_zendesk_user(self.zdp['email'])
			self.zd_enduser = self.get_zendesk_user(self.zdp['enduser'])
			
			# If the status is 'OK' and we can find the ticket 
			# matching our external_id we close the ticket
			if not self.update_zendesk_ticket(event_id,event_status):
				self.create_zendesk_ticket(event_id,event_status)
			self.db.close()
			exit(0)
		except Exception as e:
			log.error('Failed with: [%s]. Ciao!' % e)
			exit(1)

	
	def create_zendesk_ticket(self,event_id,event_status):
		collaborators = self.zbx_evt_recipients(event_id)	
		subject = self.ydata['trigger']['name']
		#description = '%s\n\nZabbix severity: %s' % (self.ydata['desc'],self.ydata['trigger']['severity'])
		description = self.ydata['desc'].replace('"','')
		priority = 'high' if self.ydata['trigger']['severity'] == 'High' else 'normal'
		tkt_data = { 'ticket': {
			'subject': subject,
			'description': description,
			'collaborators': collaborators,
			'set_tags': ['test'],
			'external_id': event_id,
			'priority': priority,
			'requester_id': self.zd_enduser['id'],
			'submitter_id': self.zd_enduser['id'],
			'organization_id': self.zd_enduser['organization_id'],
		}}
		### Auto-close if status is "OK" and severity is "information"
		if event_status == 'OK' and self.ydata['trigger']['severity'] == 'Information':
			tkt_data['ticket']['status'] = 'solved'
			tkt_data['ticket']['assignee_id'] = self.zd_user['id']

		### TODO: ADD TKT_ID as acknowledge comment
		tkt_url = self.zd.create_ticket(data=tkt_data)
		tkt_id = get_id_from_url(tkt_url)
		#tkt = self.zd.show_ticket(ticket_id=tkt_id)
		#log.debug('Created ticket with ID %s'%tkt_id)
		#log.debug(json.dumps(tkt,sort_keys=True,indent=2))
		log.info('Created Zendesk ticket ID %s from Zabbix eventid %s' % (tkt_id,event_id))
		#return tkt_id


	def update_zendesk_ticket(self,event_id,event_status):
		if event_status == 'OK':
			tkt = self.zd.list_all_tickets(external_id=event_id)
			log.debug(tkt) # json.dumps(tkt,sort_keys=True,indent=2)
			if tkt['count']==1:
				tkt_id = tkt['tickets'][0]['id']
				desc = self.ydata['desc'].replace('"','')
				#log.info('Update ticket %s' % tkt_id)
				if self.ydata['trigger']['severity'] == 'High':
					tkt_data = {'ticket':{
						'comment':{'public':True, 'body': desc}
					}}
					log.info('Updating ticket %s from Zabbix event %s' % (tkt_id,event_id))
				else:
					tkt_data = {'ticket':{
						'status': 'solved',
						'assignee_id': self.zd_user['id'],
						'comment':{
							'public':True, 
							'author_id': self.zd_enduser['id'],
							'body': '%s\n(Auto-closed by Zabbix)'%desc
						}
					}}
					log.info('Closing Zendesk ticket %s, event id: %s' % (tkt_id,event_id))

				tkt_up = self.zd.update_ticket(ticket_id=tkt_id,data=tkt_data)
				#log.debug(json.dumps(tkt_up,sort_keys=True,indent=2))
				return True
		return False

	
	def mysql_setup(self):
		try:
			# Get MySQL connection parameters from zabbix conf file
			with open(self.zabbix_conf) as f:
				my = dict( ln.lower()[2:].split('=') for ln in
					f.read().split('\n') if ln.startswith('DB') )
			self.db = MySQLdb.connect(my['host'], my['user'], my['password'], my['name'])
			self.mycsr = self.db.cursor()
		except IOError as e:
			log.error(e)
			return False
		except KeyError as e: 
			log.error('Could not find %s'%e)
			return False
		else:
			return True


	def get_zendesk_user(self,email):
		cache_file = '/tmp/zendesk_user_%s' % email
		try:
			if time()-path.getmtime(cache_file) > 86400:
				remove(cache_file)
				log.debug('Cache file deleted')
			with open(cache_file, 'r') as f: 
				data = yaml.load(f.read())
		except:
			data = self.zd.search_user(query='email:%s'%email)['users'][0]
			with open(cache_file, 'w') as f: 
				f.write(yaml.dump(data) )
		
		return data
			

	def zbx_evt_recipients(self,event_id):
		rows = ['test@example.com']
		try:
			sql = """SELECT m.sendto mail
FROM events e,functions f,items i, hosts_groups hg, groups g, users_groups uxg, usrgrp ug, media m
WHERE e.eventid=%s 
AND e.object=0 
AND e.source=0 
AND e.objectid=f.triggerid 
AND f.itemid=i.itemid 
AND hg.hostid=i.hostid 
AND ug.usrgrpid=uxg.usrgrpid
AND m.userid=uxg.userid 
AND LOWER(g.name)=LOWER(ug.name) 
AND hg.groupid=g.groupid;"""
			self.mycsr.execute(sql % event_id)
			rows.extend([ r[0] for r in self.mycsr.fetchall() ])
			return rows
		except:
			return rows

### END OF CLASS ###

if __name__ == '__main__':
	try:
		exit(z2z())
	except Exception as e:
		log.error(e)

