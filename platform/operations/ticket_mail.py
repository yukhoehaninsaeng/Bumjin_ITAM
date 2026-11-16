"""Opt-in transactional SMTP queue and authenticated IMAP replies."""
import hashlib
import imaplib
import os
import re
import secrets
import ssl
from datetime import timedelta
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from urllib.parse import urlsplit
from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.html import escape, strip_tags
from . import policies
from .models import Employee, Ticket, TicketMail, InboundTicketMail


def config(key, default=''):
    return getattr(settings,'ITAM_MAIL',{}).get(key,os.environ.get('ITAM_MAIL_'+key,default))


def enabled(key='ENABLED'):
    return str(config(key,'0')).lower() in ('1','true')


def address(user):
    if not user.is_active:return ''
    employee=Employee.objects.filter(user=user).select_related('company').first()
    if employee and (not employee.active or not employee.company.active):return ''
    result=(employee.email if employee and employee.email else user.email).strip().lower()
    try:validate_email(result)
    except ValidationError:return ''
    return result


def allowed(item):
    return address(item.recipient)==item.address and policies.tickets_visible(item.recipient,portal=item.portal).filter(pk=item.ticket_id).exists()


def enqueue(ticket,kind,actor=None):
    if not enabled():return
    recipients=[]
    if ticket.requester.user_id:recipients.append((ticket.requester.user,True))
    if ticket.assignee_id:recipients.append((ticket.assignee,False))
    elif kind in ('created','reply'):recipients.extend((u,False) for u in ticket.team.members.filter(is_active=True))
    seen=set()
    for user,portal in recipients:
        if user.pk in seen or (kind in ('reply','status') and actor and actor.pk==user.pk):continue
        seen.add(user.pk);email=address(user)
        if email and policies.tickets_visible(user,portal=portal).filter(pk=ticket.pk).exists():
            domain=str(config('FROM_EMAIL')).rsplit('@',1)[-1]
            if not re.fullmatch(r'[A-Za-z0-9.-]+',domain):domain='itam.invalid'
            TicketMail.objects.create(ticket=ticket,recipient=user,address=email,portal=portal,kind=kind,
                message_id='<'+secrets.token_hex(32)+'@'+domain+'>')


def build(item,connection=None):
    base=str(config('PUBLIC_BASE_URL')).rstrip('/');parsed=urlsplit(base)
    if (parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password
        or parsed.path or parsed.query or parsed.fragment or any(c.isspace() for c in base)):
        raise ValueError('PUBLIC_BASE_URL')
    if not settings.DEBUG and parsed.scheme!='https':raise ValueError('HTTPS_REQUIRED')
    ticket=item.ticket
    title={'created':'문의가 접수되었습니다.','assigned':'티켓이 담당자에게 배정되었습니다.',
        'reply':'문의에 새 답변이 등록되었습니다.','status':'문의 처리 상태가 변경되었습니다.'}[item.kind]
    url=base+('/portal/requests' if item.portal else '/admin/tickets')+'?ticket='+str(ticket.id)
    label='내 문의 바로가기' if item.portal else '티켓 바로가기'
    summary=f'{title}\n번호: {ticket.number}\n제목: {ticket.title}\n담당자: {ticket.assignee.get_username() if ticket.assignee_id else "미배정"}\n상태: {ticket.get_status_display()}'
    guidance='이 메일에 답장하면 공개 답글로 등록됩니다. 아래 구분선 위에 새 내용만 작성하세요. 첨부는 문의 화면에서 등록하세요.' if enabled('INBOUND_ENABLED') else '답변은 문의 화면에서 작성하세요.'
    sender=str(config('FROM_EMAIL'));reply=str(config('REPLY_ADDRESS',sender))
    validate_email(sender);validate_email(reply)
    message=EmailMultiAlternatives(subject=f'[{ticket.number}] {title}',body=summary+'\n\n'+label+': '+url+'\n\n'+guidance+'\n--- ITAM 원본 알림 ---',
        from_email=sender,to=[item.address],reply_to=[reply],connection=connection,
        headers={'Message-ID':item.message_id,'Auto-Submitted':'auto-generated','X-Auto-Response-Suppress':'All'})
    message.attach_alternative('<div style="font-family:Arial,sans-serif;max-width:600px;padding:24px"><h2>'+str(escape(title))+'</h2><p style="white-space:pre-line">'+str(escape(summary))+'</p><p><a style="display:inline-block;background:#dd254c;color:white;padding:14px 20px;text-decoration:none;border-radius:6px" href="'+str(escape(url))+'">'+label+'</a></p><p>'+str(escape(guidance))+'</p><hr>--- ITAM 원본 알림 ---</div>','text/html')
    return message


def smtp():
    tls=enabled('SMTP_USE_TLS');secure=enabled('SMTP_USE_SSL')
    if tls==secure or not config('SMTP_HOST'):raise ValueError('SMTP_CONFIGURATION')
    return get_connection('django.core.mail.backends.smtp.EmailBackend',host=config('SMTP_HOST'),port=int(config('SMTP_PORT','587')),
        username=config('SMTP_USER'),password=config('SMTP_PASSWORD'),use_tls=tls,use_ssl=secure,timeout=20)


def dispatch(limit=50):
    report={'sent':0,'failed':0,'skipped':0}
    if not enabled():return {'disabled':True}
    ids=list(TicketMail.objects.filter(state='pending',available_at__lte=timezone.now()).order_by('created_at').values_list('pk',flat=True)[:limit])
    for pk in ids:
        with transaction.atomic():
            item=TicketMail.objects.select_for_update().select_related('ticket__assignee','recipient').get(pk=pk)
            if item.state!='pending' or item.available_at>timezone.now():continue
            if not allowed(item):item.state='skipped';report['skipped']+=1
            else:
                item.attempts+=1
                try:
                    with smtp() as connection:
                        if build(item,connection).send()!=1:raise OSError('not_sent')
                    item.state='sent';item.sent_at=timezone.now();item.error_code='';report['sent']+=1
                except Exception as error:
                    item.error_code=type(error).__name__[:64];item.state='failed' if item.attempts>=5 else 'pending'
                    item.available_at=timezone.now()+timedelta(minutes=min(60,2**item.attempts));report['failed']+=1
            item.save()
    return report


def new_text(message):
    part=message.get_body(preferencelist=('plain','html'))
    if not part:return ''
    body=part.get_content()
    if part.get_content_type()=='text/html':
        body=re.sub(r'<(script|style|blockquote)\b[^>]*>.*?</\1>','',body,flags=re.S|re.I)
        body=strip_tags(re.sub(r'<(?:br|/p|/div)\b[^>]*>','\n',body,flags=re.I))
    lines=[]
    for line in body.splitlines():
        text=line.strip()
        if (text.startswith('>') or text=='--- ITAM 원본 알림 ---' or text=='--'
            or re.match(r'^(On .+wrote:|From:|보낸 사람:|-----Original Message-----)',text,re.I)):break
        lines.append(line)
    return '\n'.join(lines).strip()


def authenticated(message,sender):
    trusted=str(config('AUTH_SERV_ID')).strip().lower()
    if not trusted:return False
    domain=sender.rsplit('@',1)[-1]
    for header in message.get_all('Authentication-Results',[]):
        parts=str(header).split(';')
        if parts[0].strip().lower()!=trusted:continue
        if any(re.search(r'^\s*dmarc\s*=\s*pass\b',p,re.I) and re.search(r'\bheader\.from\s*=\s*"?'+re.escape(domain)+r'"?(?:\s|$)',p,re.I) for p in parts[1:]):return True
    return False


def receive(raw):
    if not enabled('INBOUND_ENABLED'):return 'disabled'
    if len(raw)>2097152:return 'too_large'
    message=BytesParser(policy=policy.default).parsebytes(raw)
    senders=getaddresses(message.get_all('From',[]));sender=senders[0][1].lower() if len(senders)==1 else ''
    mid=str(message.get('Message-ID',''))
    key=hashlib.sha256((sender+'\n'+mid).encode() if mid else raw).hexdigest()
    def reject(reason):
        InboundTicketMail.objects.get_or_create(key=key,defaults={'outcome':reason});return reason
    if InboundTicketMail.objects.filter(pk=key).exists():return 'duplicate'
    if (str(message.get('Auto-Submitted','no')).lower()!='no' or message.get('List-Id') or message.get('Return-Path')=='<>'
        or str(message.get('Precedence','')).lower() in ('bulk','list','junk') or message.get_content_type()=='multipart/report'):return reject('automatic')
    if not sender or len(message.get_all('From',[]))!=1 or not authenticated(message,sender):return reject('sender_not_authenticated')
    refs=re.findall(r'<[a-f0-9]{64}@[A-Za-z0-9.-]+>',' '.join(str(v) for k in ('In-Reply-To','References') for v in message.get_all(k,[])))
    if not refs or len(refs)>50:return reject('invalid_reference')
    routes=list(TicketMail.objects.filter(message_id__in=refs,address=sender,state='sent').select_related('recipient','ticket'))
    if not routes:return reject('unknown_reply')
    if len({(r.ticket_id,r.recipient_id) for r in routes})!=1:return reject('ambiguous_reply')
    route=max(routes,key=lambda r:r.sent_at or r.created_at)
    if not route.sent_at or route.sent_at<timezone.now()-timedelta(days=90):return reject('expired_reply')
    try:body=new_text(message)
    except (ValueError,LookupError,UnicodeError):return reject('invalid_body')
    if not body or len(body)>10000:return reject('invalid_body')
    if any(p.get_content_disposition()=='attachment' for p in message.walk()):body+='\n\n[메일 첨부는 등록되지 않았습니다. 문의 화면에서 첨부하세요.]'
    with transaction.atomic():
        from django.contrib.auth import get_user_model
        route.recipient=get_user_model().objects.select_for_update().get(pk=route.recipient_id)
        ticket=Ticket.objects.select_for_update().get(pk=route.ticket_id)
        if InboundTicketMail.objects.filter(pk=key).exists():return 'duplicate'
        if not allowed(route):return reject('access_revoked')
        if ticket.status in ('CLOSED','CANCELLED'):return reject('ticket_closed')
        if InboundTicketMail.objects.filter(message__author=route.recipient,received_at__gte=timezone.now()-timedelta(hours=1)).count()>=50:return reject('rate_limited')
        from .services import add_ticket_message
        reply=add_ticket_message(route.recipient,ticket.id,body,portal=route.portal,source='email')
        InboundTicketMail.objects.create(key=key,outcome='registered',message=reply)
    return 'registered'


def poll(limit=50):
    if not enabled('INBOUND_ENABLED'):return {'disabled':True}
    if any(not config(k) for k in ('IMAP_HOST','IMAP_USER','IMAP_PASSWORD','AUTH_SERV_ID')):return {'error':'configuration_incomplete'}
    connection=None;report={}
    try:
        connection=imaplib.IMAP4_SSL(config('IMAP_HOST'),int(config('IMAP_PORT','993')),ssl_context=ssl.create_default_context(cafile=config('IMAP_CA_FILE') or None),timeout=20)
        connection.login(config('IMAP_USER'),config('IMAP_PASSWORD'))
        if connection.select(config('IMAP_FOLDER','INBOX'))[0]!='OK':raise OSError('select')
        status,data=connection.uid('search',None,'UNSEEN')
        if status!='OK':raise OSError('search')
        for uid in (data[0] or b'').split()[:limit]:
            status,parts=connection.uid('fetch',uid,'(BODY.PEEK[]<0.2097153>)')
            chunks=[p[1] for p in parts if isinstance(p,tuple) and isinstance(p[1],bytes)]
            if status!='OK' or len(chunks)!=1:raise OSError('fetch')
            outcome=receive(chunks[0]);report[outcome]=report.get(outcome,0)+1
            if connection.uid('store',uid,'+FLAGS.SILENT','(\\Seen)')[0]!='OK':raise OSError('ack')
    except (OSError,ValueError,imaplib.IMAP4.error) as error:report['error']=type(error).__name__
    finally:
        if connection:
            try:connection.logout()
            except (OSError,imaplib.IMAP4.error):pass
    return report
