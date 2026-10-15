"""
Script to add profile photo columns to the SQLite database.
Run this script to update the existing database schema.
"""

import sqlite3
import sys


def add_photo_columns(db_path: str = "attendance.db"):
    """Add profile photo columns to students table."""
    
    print(f"Opening database: {db_path}")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Check current schema
        cursor.execute("PRAGMA table_info(students)")
        columns = [col[1] for col in cursor.fetchall()]
        print(f"Current columns in students table: {columns}")
        
        # Check which columns need to be added
        columns_to_add = []
        
        if "profile_photo_url" not in columns:
            columns_to_add.append("profile_photo_url VARCHAR(500)")
        
        if "profile_photo_public_id" not in columns:
            columns_to_add.append("profile_photo_public_id VARCHAR(200)")
        
        if "profile_photo_format" not in columns:
            columns_to_add.append("profile_photo_format VARCHAR(10)")
        
        if "profile_photo_updated_at" not in columns:
            columns_to_add.append("profile_photo_updated_at DATETIME")
        
        if not columns_to_add:
            print("All photo columns already exist.")
            return
        
        # SQLite doesn't support adding multiple columns in one statement
        for column_def in columns_to_add:
            column_name = column_def.split()[0]
            print(f"Adding column: {column_name}")
            
            # Check if column exists (double-check)
            cursor.execute(f"PRAGMA table_info(students)")
            existing_columns = [col[1] for col in cursor.fetchall()]
            
            if column_name not in existing_columns:
                sql = f"ALTER TABLE students ADD COLUMN {column_def}"
                print(f"Executing: {sql}")
                cursor.execute(sql)
            else:
                print(f"Column {column_name} already exists")
        
        conn.commit()
        print("Database schema updated successfully!")
        
        # Verify the changes
        cursor.execute("PRAGMA table_info(students)")
        new_columns = cursor.fetchall()
        print("\nUpdated columns in students table:")
        for col in new_columns:
            print(f"  {col[1]} ({col[2]}) - nullable: {bool(col[3])}")
        
    except Exception as e:
        print(f"Error: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else "attendance.db"
    add_photo_columns(db_path)