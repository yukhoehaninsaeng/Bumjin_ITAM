"""Scoped notices/help with staged, validated and re-encoded private images."""
import io
import uuid
from datetime import timedelta
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.exceptions import PermissionDenied,ValidationError
from rest_framework.response import Response
from .models import PortalArticle,PortalArticleImage,Company,AuditEvent
from .policies import employee_for
from .admin_settings import require_admin,paginate
from .uploads import validate_upload


class ImageSelection(serializers.Serializer):
    id=serializers.UUIDField()
    caption=serializers.CharField(max_length=200,allow_blank=True,default='')


class ArticlePayload(serializers.Serializer):
    kind=serializers.ChoiceField(choices=['NOTICE','HELP'],required=False)
    title=serializers.CharField(max_length=200,allow_blank=True,required=False)
    body=serializers.CharField(max_length=20000,allow_blank=True,required=False)
    status=serializers.ChoiceField(choices=['DRAFT','PUBLISHED','ARCHIVED'],required=False)
    company_id=serializers.UUIDField(allow_null=True,required=False)
    pinned=serializers.BooleanField(required=False)
    sort_order=serializers.IntegerField(min_value=0,max_value=9999,required=False)
    expected_version=serializers.IntegerField(min_value=1,required=False)
    images=ImageSelection(many=True,max_length=5,required=False)


def scoped(user):
    employee=employee_for(user)
    if not employee:raise PermissionDenied('재직 중인 직원 계정이 필요합니다.')
    return PortalArticle.objects.filter(status='PUBLISHED').filter(Q(company__isnull=True)|Q(company=employee.company))


def image_json(image,admin=False):
    return {'id':str(image.pk),'caption':image.caption,'url':f'/api/v1/{"admin" if admin else "portal"}/content/images/{image.pk}'}


def article_json(article,admin=False,detail=False):
    result={'id':str(article.pk),'kind':article.kind,'title':article.title,'status':article.status,'pinned':article.pinned,
        'sort_order':article.sort_order,'company_id':str(article.company_id) if article.company_id else None,'company':article.company.code if article.company_id else '전체 직원',
        'updated_at':article.updated_at.isoformat(),'published_at':article.published_at.isoformat() if article.published_at else None,'version':article.version}
    if detail:
        result['body']=article.body
        result['images']=[image_json(i,admin) for i in article.images.filter(active=True).order_by('position','created_at')]
    else:result['summary']=article.body[:140]
    return result


def parse(data):
    serializer=ArticlePayload(data=data);serializer.is_valid(raise_exception=True);return serializer.validated_data


def event(user,article,action,data=None):
    AuditEvent.objects.create(actor=user,company=article.company,action=action,data={'article_id':str(article.pk),'kind':article.kind,**(data or {})})


@api_view(['GET','POST'])
def articles(request,admin=False):
    if admin:require_admin(request.user)
    elif request.method!='GET':raise PermissionDenied()
    if request.method=='POST':
        payload=parse(request.data)
        if payload.get('status','DRAFT')!='DRAFT' or payload.get('images'):raise ValidationError('먼저 초안으로 작성한 뒤 게시하세요.')
        if 'kind' not in payload:raise ValidationError('공지/도움말 종류를 선택하세요.')
        company=None
        if payload.get('company_id'):company=get_object_or_404(Company,pk=payload['company_id'],active=True)
        with transaction.atomic():
            article=PortalArticle.objects.create(kind=payload['kind'],title=payload.get('title',''),body=payload.get('body',''),company=company,
                pinned=payload.get('pinned',False),sort_order=payload.get('sort_order',0),created_by=request.user,updated_by=request.user)
            event(request.user,article,'content.created');return Response({'data':article_json(article,True,True)},status=201)
    qs=PortalArticle.objects.all() if admin else scoped(request.user)
    kind=request.query_params.get('kind')
    if kind:
        if kind not in ('NOTICE','HELP'):raise ValidationError('게시글 종류를 확인하세요.')
        qs=qs.filter(kind=kind)
    query=str(request.query_params.get('q','')).strip()[:100]
    if query:qs=qs.filter(Q(title__icontains=query)|Q(body__icontains=query))
    if admin and request.query_params.get('status'):
        status=request.query_params['status']
        if status not in ('DRAFT','PUBLISHED','ARCHIVED'):raise ValidationError('게시 상태를 확인하세요.')
        qs=qs.filter(status=status)
    return paginate(request,qs.select_related('company').order_by('-pinned','sort_order','-published_at','-updated_at','id'),lambda a:article_json(a,admin))


@api_view(['GET','PATCH','DELETE'])
def article_detail(request,article_id,admin=False):
    if admin:require_admin(request.user)
    elif request.method!='GET':raise PermissionDenied()
    if request.method=='GET':
        article=get_object_or_404(PortalArticle.objects.select_related('company') if admin else scoped(request.user).select_related('company'),pk=article_id)
        return Response({'data':article_json(article,admin,True)})
    payload=parse(request.data)
    with transaction.atomic():
        article=get_object_or_404(PortalArticle.objects.select_for_update(),pk=article_id)
        if payload.get('expected_version')!=article.version:raise ValidationError('다른 관리자가 글을 수정했습니다. 다시 열어 최신 내용을 확인하세요.')
        if request.method=='DELETE':article.status='ARCHIVED'
        else:
            if 'company_id' in payload:article.company=get_object_or_404(Company,pk=payload['company_id'],active=True) if payload['company_id'] else None
            for name in ('kind','title','body','status','pinned','sort_order'):
                if name in payload:setattr(article,name,payload[name])
            if 'images' in payload:
                selected=payload['images'];ids=[i['id'] for i in selected]
                if len(ids)!=len(set(ids)):raise ValidationError('동일 이미지를 중복 첨부할 수 없습니다.')
                images={i.pk:i for i in article.images.filter(pk__in=ids)}
                if len(images)!=len(ids):raise ValidationError('해당 글에 업로드한 이미지만 첨부할 수 있습니다.')
                article.images.exclude(pk__in=ids).update(active=False)
                for position,item in enumerate(selected):
                    image=images[item['id']];image.active=True;image.position=position;image.caption=item['caption'];image.save(update_fields=['active','position','caption'])
            if article.status=='PUBLISHED':
                if not article.title.strip() or (not article.body.strip() and not article.images.filter(active=True).exists()):raise ValidationError('제목과 본문 또는 이미지를 입력한 뒤 게시하세요.')
                if not article.published_at:article.published_at=timezone.now()
        article.version+=1;article.updated_by=request.user;article.updated_at=timezone.now();article.save()
        event(request.user,article,'content.archived' if request.method=='DELETE' else 'content.updated',{'status':article.status,'version':article.version})
        if request.method!='DELETE' and 'images' in payload:
            keep=[i['id'] for i in payload['images']]
            for image in list(article.images.exclude(pk__in=keep)):
                storage=image.file.storage;name=image.file.name;image.delete()
                transaction.on_commit(lambda storage=storage,name=name:storage.delete(name),robust=True)
    return Response({'data':article_json(article,True,True)})


@api_view(['POST'])
def upload_image(request,article_id):
    require_admin(request.user)
    upload=request.FILES.get('file')
    if not upload or upload.size>5*1024*1024:raise ValidationError('5MB 이하 PNG/JPG/WEBP 이미지를 선택하세요.')
    metadata=validate_upload(upload)
    if not metadata['is_image']:raise ValidationError('이미지 파일만 첨부할 수 있습니다.')
    from PIL import Image,ImageOps
    try:
        with Image.open(upload) as source:
            if source.width*source.height>20_000_000:raise ValueError()
            image=ImageOps.exif_transpose(source);image.thumbnail((1600,1600))
            if image.mode not in ('RGB','RGBA'):image=image.convert('RGBA' if 'transparency' in image.info else 'RGB')
            encoded=io.BytesIO();image.save(encoded,format='WEBP',quality=85)
    except (OSError,ValueError):raise ValidationError('이미지 크기/형식을 확인하세요. 최대 2천만 화소입니다.')
    path=None
    try:
        with transaction.atomic():
            article=get_object_or_404(PortalArticle.objects.select_for_update(),pk=article_id)
            try:version=int(request.data.get('expected_version','0'))
            except (ValueError,TypeError):raise ValidationError('게시글 버전을 확인하세요.')
            if version!=article.version:raise ValidationError('글이 변경되었습니다. 다시 열어주세요.')
            if article.images.count()>=10:raise ValidationError('대기 중인 이미지가 많습니다. 글을 저장하거나 정리 후 다시 시도하세요.')
            record=PortalArticleImage(article=article)
            record.file.save(str(uuid.uuid4())+'.webp',ContentFile(encoded.getvalue()),save=False);path=record.file.name;record.save()
            event(request.user,article,'content.image_uploaded',{'image_id':str(record.pk)})
    except Exception:
        if path:PortalArticleImage._meta.get_field('file').storage.delete(path)
        raise
    return Response({'data':image_json(record,True)},status=201)


@api_view(['GET'])
def image_file(request,image_id,admin=False):
    if admin:require_admin(request.user)
    image=get_object_or_404(PortalArticleImage.objects.select_related('article'),pk=image_id)
    if not admin and (not image.active or not scoped(request.user).filter(pk=image.article_id).exists()):
        from rest_framework.exceptions import NotFound
        raise NotFound()
    response=FileResponse(image.file.open('rb'),content_type='image/webp',filename='article-image.webp')
    response['Cache-Control']='private, no-store';response['X-Content-Type-Options']='nosniff';return response


def cleanup_images():
    count=0;before=timezone.now()-timedelta(days=1)
    for pk in list(PortalArticleImage.objects.filter(active=False,created_at__lt=before).values_list('pk',flat=True)):
        with transaction.atomic():
            image=PortalArticleImage.objects.filter(pk=pk).first()
            if not image:continue
            # Lock the same parent used by publish/upload so an image cannot be deleted while being attached.
            PortalArticle.objects.select_for_update().get(pk=image.article_id)
            image=PortalArticleImage.objects.filter(pk=pk).first()
            if not image or image.active:continue
            storage=image.file.storage;name=image.file.name;image.delete()
            transaction.on_commit(lambda storage=storage,name=name:storage.delete(name));count+=1
    return count
