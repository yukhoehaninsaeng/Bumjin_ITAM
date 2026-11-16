import io,tempfile
from datetime import timedelta
from pathlib import Path
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase,override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory,force_authenticate
from PIL import Image
from .models import Company,Organization,Employee,RoleGrant,PortalArticle,PortalArticleImage,AuditEvent
from . import portal_content as api


class PortalContentTests(TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.media=override_settings(MEDIA_ROOT=self.folder.name);self.media.enable()
        self.addCleanup(self.folder.cleanup);self.addCleanup(self.media.disable)
        User=get_user_model();self.admin=User.objects.create_user(username='admin');self.reader=User.objects.create_user(username='reader');self.other=User.objects.create_user(username='other');self.local=User.objects.create_user(username='local')
        self.company=Company.objects.create(code='BJE',name='BJE');self.foreign=Company.objects.create(code='BJC',name='BJC')
        self.org=Organization.objects.create(company=self.company,name='IT');self.otherorg=Organization.objects.create(company=self.foreign,name='IT')
        self.employee=Employee.objects.create(user=self.reader,company=self.company,organization=self.org,name='직원')
        Employee.objects.create(user=self.other,company=self.foreign,organization=self.otherorg,name='다른법인')
        RoleGrant.objects.create(user=self.admin,role='system_admin',scope_type='GLOBAL')
        self.factory=APIRequestFactory()
    def request(self,view,method='get',data=None,user=None,**kwargs):
        request=getattr(self.factory,method)('/api/content',data or {},format='json');force_authenticate(request,user=user or self.admin);return view(request,**kwargs)
    def article(self,**kwargs):
        defaults={'kind':'NOTICE','title':'공지 제목','body':'안내 본문','created_by':self.admin,'updated_by':self.admin};defaults.update(kwargs);return PortalArticle.objects.create(**defaults)
    def image(self,name='screenshot.png'):
        data=io.BytesIO();Image.new('RGB',(2200,200),(255,0,0)).save(data,'PNG')
        return SimpleUploadedFile(name,data.getvalue(),content_type='image/png')
    def upload(self,article,file=None,user=None,version=None):
        request=self.factory.post('/api/image',{'file':file or self.image(),'expected_version':version or article.version},format='multipart');force_authenticate(request,user=user or self.admin)
        return api.upload_image(request,article_id=article.pk)
    def publish(self,article,**kwargs):
        return self.request(api.article_detail,'patch',{'expected_version':article.version,'status':'PUBLISHED',**kwargs},article_id=article.pk,admin=True)
    def test_staff_can_read_only_published_global_and_own_company(self):
        self.article(status='PUBLISHED',published_at=timezone.now());self.article(status='PUBLISHED',company=self.company,published_at=timezone.now());hidden=self.article(status='PUBLISHED',company=self.foreign);self.article(status='DRAFT');self.article(status='ARCHIVED')
        result=self.request(api.articles,user=self.reader).data;self.assertEqual(result['meta']['total'],2)
        self.assertEqual(self.request(api.article_detail,user=self.reader,article_id=hidden.pk).status_code,404)
    def test_unlinked_or_retired_cannot_read(self):
        self.article(status='PUBLISHED')
        self.assertEqual(self.request(api.articles,user=self.local).status_code,403)
        self.employee.active=False;self.employee.save();self.assertEqual(self.request(api.articles,user=self.reader).status_code,403)
    def test_non_system_admin_cannot_create_or_upload(self):
        self.assertEqual(self.request(api.articles,'post',{'kind':'HELP'},user=self.reader,admin=True).status_code,403)
        self.assertEqual(self.upload(self.article(),user=self.reader).status_code,403)
        self.assertEqual(self.request(api.articles,'post',{'kind':'NOTICE'},user=self.reader).status_code,403)
    def test_create_draft_and_publish_validation(self):
        r=self.request(api.articles,'post',{'kind':'HELP'},admin=True);self.assertEqual(r.status_code,201)
        item=PortalArticle.objects.get(pk=r.data['data']['id']);self.assertEqual(item.status,'DRAFT')
        self.assertEqual(self.publish(item).status_code,400)
        self.assertEqual(self.publish(item,title='VPN 안내',body='접속 방법').status_code,200)
        item.refresh_from_db();self.assertIsNotNone(item.published_at)
    def test_stale_edit_and_archive(self):
        article=self.article();self.publish(article)
        self.assertEqual(self.request(api.article_detail,'patch',{'title':'stale','expected_version':1},article_id=article.pk,admin=True).status_code,400)
        article.refresh_from_db();r=self.request(api.article_detail,'delete',{'expected_version':article.version},article_id=article.pk,admin=True)
        self.assertEqual(r.status_code,200);self.assertEqual(self.request(api.article_detail,user=self.reader,article_id=article.pk).status_code,404)
        self.assertTrue(AuditEvent.objects.filter(action='content.archived').exists())
    def test_image_staged_private_reencoded_and_resized(self):
        article=self.article(status='PUBLISHED');r=self.upload(article);self.assertEqual(r.status_code,201)
        image=PortalArticleImage.objects.get(pk=r.data['data']['id']);self.assertFalse(image.active)
        with Image.open(image.file.path) as loaded:self.assertEqual(loaded.format,'WEBP');self.assertLessEqual(max(loaded.size),1600)
        self.assertEqual(self.request(api.image_file,user=self.reader,image_id=image.pk).status_code,404)
        response=self.request(api.image_file,image_id=image.pk,admin=True);self.assertEqual(response.status_code,200);self.assertEqual(response['Content-Type'],'image/webp');response.close()
    def test_image_requires_published_same_company_parent(self):
        article=self.article(company=self.company);r=self.upload(article);image_id=r.data['data']['id']
        r=self.publish(article,images=[{'id':image_id,'caption':'접속 화면'}]);self.assertEqual(r.status_code,200)
        image=PortalArticleImage.objects.get(pk=image_id)
        response=self.request(api.image_file,user=self.reader,image_id=image.pk);self.assertEqual(response.status_code,200);response.close()
        self.assertEqual(self.request(api.image_file,user=self.other,image_id=image.pk).status_code,404)
        article.refresh_from_db();self.request(api.article_detail,'delete',{'expected_version':article.version},admin=True,article_id=article.pk)
        self.assertEqual(self.request(api.image_file,user=self.reader,image_id=image.pk).status_code,404)
    def test_disguised_image_nonimage_and_oversize_rejected(self):
        article=self.article()
        fake=SimpleUploadedFile('fake.png',b'<script>bad()</script>',content_type='image/png')
        self.assertEqual(self.upload(article,fake).status_code,400)
        text=SimpleUploadedFile('note.txt',b'hello',content_type='text/plain');self.assertEqual(self.upload(article,text).status_code,400)
        large=SimpleUploadedFile('large.png',b'x'*(5*1024*1024+1),content_type='image/png');self.assertEqual(self.upload(article,large).status_code,400)
        self.assertFalse(PortalArticleImage.objects.exists())
    def test_other_article_image_cannot_be_attached(self):
        a=self.article();b=self.article();image_id=self.upload(a).data['data']['id']
        self.assertEqual(self.publish(b,images=[{'id':image_id,'caption':''}]).status_code,400)
        b.refresh_from_db();self.assertEqual(b.status,'DRAFT')
    def test_images_limit_and_order_caption(self):
        article=self.article();first=self.upload(article).data['data']['id'];second=self.upload(article).data['data']['id']
        r=self.publish(article,images=[{'id':second,'caption':'두 번째'},{'id':first,'caption':'첫 번째'}]);self.assertEqual(r.status_code,200)
        self.assertEqual(r.data['data']['images'][0]['caption'],'두 번째')
        article.refresh_from_db();self.assertEqual(self.request(api.article_detail,'patch',{'expected_version':article.version,'images':[{'id':first}]*6},admin=True,article_id=article.pk).status_code,400)
    def test_unattached_image_cleanup_keeps_attached(self):
        a=self.article();b=self.article();staged=self.upload(a).data['data']['id'];attached=self.upload(b).data['data']['id'];self.publish(b,images=[{'id':attached}])
        PortalArticleImage.objects.update(created_at=timezone.now()-timedelta(days=2));image=PortalArticleImage.objects.get(pk=staged);path=image.file.path
        with self.captureOnCommitCallbacks(execute=True):self.assertEqual(api.cleanup_images(),1)
        self.assertFalse(Path(path).exists());self.assertTrue(PortalArticleImage.objects.filter(pk=attached).exists())
    def test_search_kind_and_pinned_order(self):
        self.article(status='PUBLISHED',title='VPN 접속',body='접속 방법',kind='HELP');pinned=self.article(status='PUBLISHED',title='VPN 공통',kind='HELP',pinned=True);self.article(status='PUBLISHED',title='다른 공지',kind='NOTICE')
        request=self.factory.get('/api',{'kind':'HELP','q':'VPN'});force_authenticate(request,user=self.reader)
        result=api.articles(request).data;self.assertEqual(result['meta']['total'],2);self.assertEqual(result['data'][0]['id'],str(pinned.pk))
    def test_removed_image_is_not_accessible(self):
        a=self.article();image_id=self.upload(a).data['data']['id'];self.publish(a,images=[{'id':image_id}]);a.refresh_from_db()
        self.request(api.article_detail,'patch',{'expected_version':a.version,'images':[]},admin=True,article_id=a.pk)
        self.assertEqual(self.request(api.image_file,user=self.reader,image_id=image_id).status_code,404)
    def test_image_upload_version_conflict_leaves_no_file(self):
        a=self.article(version=3);self.assertEqual(self.upload(a,version=1).status_code,400)
        self.assertFalse(PortalArticleImage.objects.exists());self.assertFalse(list(Path(self.folder.name).rglob('*.webp')))
