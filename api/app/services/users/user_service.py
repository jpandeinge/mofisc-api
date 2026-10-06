from typing import Any, Dict

from sqlalchemy.orm import Session

from ...errors.exceptions import DatabaseError, NotFoundError, ValidationError
from ...models.user import User, UserProfile
from ...sentry import set_sentry_context


class UserService:
    @staticmethod
    async def get_user_profile(user_id: str, db: Session) -> Dict[str, Any]:
        """Get user profile with related data."""
        try:
            profile = db.query(UserProfile).filter(UserProfile.id == user_id).first()

            if not profile:
                raise NotFoundError("UserProfile", user_id)

            # Include any additional user-specific data
            transactions_count = (
                db.query(User)
                .filter(User.id == user_id)
                .join(User.transactions)
                .count()
            )

            return {
                "profile": {
                    "id": profile.id,
                    "full_name": profile.full_name,
                    "email": profile.email,
                    "phone": profile.phone,
                    "user_type": profile.user_type,
                    "profile_picture": profile.profile_picture,
                    "preferences": profile.preferences or {},
                    "created_at": profile.created_at,
                    "updated_at": profile.updated_at,
                    "is_active": profile.is_active,
                    "organisation_id": profile.organisation_id,
                },
                "stats": {
                    "transactions_count": transactions_count,
                },
            }
        except Exception as e:
            set_sentry_context(
                "user_service_error",
                {"operation": "get_user_profile", "user_id": user_id, "error": str(e)},
            )
            raise DatabaseError(f"Error fetching user profile: {str(e)}")

    @staticmethod
    async def update_user_profile(
        user_id: str, updates: Dict[str, Any], db: Session
    ) -> Dict[str, Any]:
        """Update user profile data."""
        try:
            profile = db.query(UserProfile).filter(UserProfile.id == user_id).first()

            if not profile:
                raise NotFoundError("UserProfile", user_id)

            # Validate updates
            allowed_fields = {
                "full_name",
                "phone",
                "profile_picture",
                "preferences",
                "user_type",
            }

            invalid_fields = set(updates.keys()) - allowed_fields
            if invalid_fields:
                raise ValidationError(f"Invalid fields: {', '.join(invalid_fields)}")

            # Update fields
            for key, value in updates.items():
                setattr(profile, key, value)

            db.commit()
            db.refresh(profile)

            return {
                "id": profile.id,
                "full_name": profile.full_name,
                "email": profile.email,
                "phone": profile.phone,
                "user_type": profile.user_type,
                "profile_picture": profile.profile_picture,
                "preferences": profile.preferences or {},
                "updated_at": profile.updated_at,
                "is_active": profile.is_active,
                "organisation_id": profile.organisation_id,
            }

        except Exception as e:
            db.rollback()
            set_sentry_context(
                "user_service_error",
                {
                    "operation": "update_user_profile",
                    "user_id": user_id,
                    "updates": updates,
                    "error": str(e),
                },
            )
            raise DatabaseError(f"Error updating user profile: {str(e)}")

    @staticmethod
    async def create_user_profile(
        user_id: str, profile_data: Dict[str, Any], db: Session
    ) -> Dict[str, Any]:
        """Create new user profile."""
        try:
            # Check if profile already exists
            existing_profile = (
                db.query(UserProfile).filter(UserProfile.id == user_id).first()
            )

            if existing_profile:
                raise ValidationError("Profile already exists for this user")

            # Validate required fields
            required_fields = {"full_name", "email", "user_type"}
            missing_fields = required_fields - set(profile_data.keys())

            if missing_fields:
                raise ValidationError(
                    f"Missing required fields: {', '.join(missing_fields)}"
                )

            # Create new profile
            new_profile = UserProfile(id=user_id, **profile_data)

            db.add(new_profile)
            db.commit()
            db.refresh(new_profile)

            return {
                "id": new_profile.id,
                "full_name": new_profile.full_name,
                "email": new_profile.email,
                "phone": new_profile.phone,
                "user_type": new_profile.user_type,
                "profile_picture": new_profile.profile_picture,
                "created_at": new_profile.created_at,
                "is_active": new_profile.is_active,
                "organisation_id": new_profile.organisation_id,
            }

        except Exception as e:
            db.rollback()
            set_sentry_context(
                "user_service_error",
                {
                    "operation": "create_user_profile",
                    "user_id": user_id,
                    "profile_data": profile_data,
                    "error": str(e),
                },
            )
            raise DatabaseError(f"Error creating user profile: {str(e)}")
