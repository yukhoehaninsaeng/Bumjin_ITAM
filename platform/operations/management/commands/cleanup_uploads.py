from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from operations.models import Attachment, UploadDraft


class Command(BaseCommand):
    help = "Remove expired or committed draft leftovers. Defaults to preview; use --apply."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        ids = list(
            UploadDraft.objects.filter(expires_at__lt=timezone.now()).values_list("pk", flat=True)
        )
        count = 0
        for pk in ids:
            with transaction.atomic():
                draft = (
                    UploadDraft.objects.select_for_update()
                    .filter(pk=pk, expires_at__lt=timezone.now())
                    .first()
                )
                if not draft:
                    continue
                files = list(Attachment.objects.filter(draft=draft, ticket__isnull=True))
                count += len(files)
                if options["apply"]:
                    for file in files:
                        storage, name = file.file.storage, file.file.name
                        file.delete()
                        transaction.on_commit(
                            lambda storage=storage, name=name: storage.delete(name)
                        )
                    # Keep a draft referenced by committed attachments/ticket as an ownership record.
                    if draft.ticket_id is None and not draft.attachments.exists():
                        draft.delete()
        self.stdout.write(
            f"{count}개 임시 파일 " + ("정리 완료" if options["apply"] else "정리 대상 (미적용)")
        )
