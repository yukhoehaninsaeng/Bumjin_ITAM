from django.core.management.base import BaseCommand
from operations.portal_content import cleanup_images


class Command(BaseCommand):
    help='Delete images not attached to any saved article for at least 24 hours.'
    def handle(self,*args,**options):self.stdout.write(str(cleanup_images())+'개 미사용 이미지를 정리했습니다.')
