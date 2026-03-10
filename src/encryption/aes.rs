//! AES encryption/decryption for PDF.
//!
//! AES (Advanced Encryption Standard) is used in PDF 1.6+ for stronger encryption.
//! PDFs use AES in CBC (Cipher Block Chaining) mode with PKCS#7 padding.
//!
//! Supported algorithms:
//! - AES-128: 16-byte key (PDF 1.6+, V=4, R=4)
//! - AES-256: 32-byte key (PDF 2.0, V=5, R=5/6)
//!
//! PDF Spec: Section 7.6.2 - General Encryption Algorithm

use aes::cipher::{BlockDecryptMut, BlockEncryptMut, KeyIvInit};
use aes::{Aes128, Aes256};
use cbc::{Decryptor, Encryptor};

#[allow(dead_code)]
type Aes128CbcEnc = Encryptor<Aes128>;
type Aes128CbcDec = Decryptor<Aes128>;

#[allow(dead_code)]
type Aes256CbcEnc = Encryptor<Aes256>;
#[allow(dead_code)]
type Aes256CbcDec = Decryptor<Aes256>;

/// Strip PKCS#7 padding leniently.
///
/// If the last byte indicates valid PKCS#7 padding (1-16) and all padding bytes
/// match, strip them. Otherwise return data as-is. This is necessary because
/// many real-world PDFs (especially permissions-only encrypted ones) have streams
/// with invalid or missing padding.
fn strip_pkcs7_padding(data: &[u8]) -> &[u8] {
    if data.is_empty() {
        return data;
    }

    let padding_len = data[data.len() - 1] as usize;
    if padding_len == 0 || padding_len > 16 || padding_len > data.len() {
        return data;
    }

    let data_len = data.len() - padding_len;
    for &byte in &data[data_len..] {
        if byte != padding_len as u8 {
            // Invalid padding — return raw decrypted data
            return data;
        }
    }

    &data[..data_len]
}

/// Encrypt data using AES-128 in CBC mode with PKCS#7 padding.
///
/// # Arguments
///
/// * `key` - The 16-byte encryption key
/// * `iv` - The 16-byte initialization vector
/// * `data` - The data to encrypt
///
/// # Returns
///
/// The encrypted data with PKCS#7 padding, or an error if encryption fails
#[allow(dead_code)]
pub fn aes128_encrypt(key: &[u8], iv: &[u8], data: &[u8]) -> Result<Vec<u8>, &'static str> {
    if key.len() != 16 {
        return Err("AES-128 key must be 16 bytes");
    }
    if iv.len() != 16 {
        return Err("IV must be 16 bytes");
    }

    // Apply PKCS#7 padding manually
    let mut padded = data.to_vec();
    let padding_len = 16 - (data.len() % 16);
    padded.extend(std::iter::repeat_n(padding_len as u8, padding_len));

    // Encrypt in-place
    let len = padded.len();
    let cipher = Aes128CbcEnc::new(key.into(), iv.into());
    cipher
        .encrypt_padded_mut::<aes::cipher::block_padding::NoPadding>(&mut padded, len)
        .map_err(|_| "Encryption failed")?;

    Ok(padded)
}

/// Encrypt data using AES-128 in CBC mode WITHOUT padding.
///
/// Used by Algorithm 2.B (R=6) which handles its own data alignment.
/// Data length must be a multiple of 16.
pub fn aes128_encrypt_no_padding(
    key: &[u8],
    iv: &[u8],
    data: &[u8],
) -> Result<Vec<u8>, &'static str> {
    if key.len() != 16 {
        return Err("AES-128 key must be 16 bytes");
    }
    if iv.len() != 16 {
        return Err("IV must be 16 bytes");
    }
    if data.is_empty() {
        return Ok(Vec::new());
    }
    if !data.len().is_multiple_of(16) {
        return Err("Data length must be multiple of 16 for no-padding mode");
    }

    let mut buffer = data.to_vec();
    let len = buffer.len();
    let cipher = Aes128CbcEnc::new(key.into(), iv.into());
    cipher
        .encrypt_padded_mut::<aes::cipher::block_padding::NoPadding>(&mut buffer, len)
        .map_err(|_| "Encryption failed")?;

    Ok(buffer)
}

/// Encrypt data using AES-256 in CBC mode WITHOUT padding.
///
/// Used for R=5/6 file encryption key wrapping (UE/OE encryption).
/// Data length must be a multiple of 16.
pub fn aes256_encrypt_no_padding(
    key: &[u8],
    iv: &[u8],
    data: &[u8],
) -> Result<Vec<u8>, &'static str> {
    if key.len() != 32 {
        return Err("AES-256 key must be 32 bytes");
    }
    if iv.len() != 16 {
        return Err("IV must be 16 bytes");
    }
    if data.is_empty() {
        return Ok(Vec::new());
    }
    if !data.len().is_multiple_of(16) {
        return Err("Data length must be multiple of 16 for no-padding mode");
    }

    let mut buffer = data.to_vec();
    let len = buffer.len();
    let cipher = Aes256CbcEnc::new(key.into(), iv.into());
    cipher
        .encrypt_padded_mut::<aes::cipher::block_padding::NoPadding>(&mut buffer, len)
        .map_err(|_| "Encryption failed")?;

    Ok(buffer)
}

/// Decrypt data using AES-256 in CBC mode WITHOUT padding.
///
/// Used for R=6 file encryption key unwrapping (UE/OE decryption).
/// Data length must be a multiple of 16.
pub fn aes256_decrypt_no_padding(
    key: &[u8],
    iv: &[u8],
    data: &[u8],
) -> Result<Vec<u8>, &'static str> {
    if key.len() != 32 {
        return Err("AES-256 key must be 32 bytes");
    }
    if iv.len() != 16 {
        return Err("IV must be 16 bytes");
    }
    if data.is_empty() {
        return Ok(Vec::new());
    }
    if !data.len().is_multiple_of(16) {
        return Err("Data length must be multiple of 16 for no-padding mode");
    }

    let mut buffer = data.to_vec();
    let cipher = Aes256CbcDec::new(key.into(), iv.into());
    cipher
        .decrypt_padded_mut::<aes::cipher::block_padding::NoPadding>(&mut buffer)
        .map_err(|_| "Decryption failed")?;

    Ok(buffer)
}

/// Decrypt data using AES-128 in CBC mode and remove PKCS#7 padding.
///
/// # Arguments
///
/// * `key` - The 16-byte encryption key
/// * `iv` - The 16-byte initialization vector
/// * `data` - The encrypted data
///
/// # Returns
///
/// The decrypted data with padding removed, or an error if decryption fails.
/// If PKCS#7 padding is invalid (common in real-world PDFs), the raw decrypted
/// data is returned without unpadding rather than failing.
pub fn aes128_decrypt(key: &[u8], iv: &[u8], data: &[u8]) -> Result<Vec<u8>, &'static str> {
    if key.len() != 16 {
        return Err("AES-128 key must be 16 bytes");
    }
    if iv.len() != 16 {
        return Err("IV must be 16 bytes");
    }
    if data.is_empty() {
        return Ok(Vec::new());
    }
    if !data.len().is_multiple_of(16) {
        return Err("Encrypted data length must be multiple of 16");
    }

    // Decrypt in-place
    let mut buffer = data.to_vec();
    let cipher = Aes128CbcDec::new(key.into(), iv.into());
    let decrypted = cipher
        .decrypt_padded_mut::<aes::cipher::block_padding::NoPadding>(&mut buffer)
        .map_err(|_| "Decryption failed")?;

    Ok(strip_pkcs7_padding(decrypted).to_vec())
}

/// Encrypt data using AES-256 in CBC mode with PKCS#7 padding.
///
/// Used for PDF 2.0 encryption (V=5, R=5/6).
///
/// # Arguments
///
/// * `key` - The 32-byte encryption key
/// * `iv` - The 16-byte initialization vector
/// * `data` - The data to encrypt
///
/// # Returns
///
/// The encrypted data with PKCS#7 padding, or an error if encryption fails
#[allow(dead_code)]
pub fn aes256_encrypt(key: &[u8], iv: &[u8], data: &[u8]) -> Result<Vec<u8>, &'static str> {
    if key.len() != 32 {
        return Err("AES-256 key must be 32 bytes");
    }
    if iv.len() != 16 {
        return Err("IV must be 16 bytes");
    }

    // Apply PKCS#7 padding manually
    let mut padded = data.to_vec();
    let padding_len = 16 - (data.len() % 16);
    padded.extend(std::iter::repeat_n(padding_len as u8, padding_len));

    // Encrypt in-place
    let len = padded.len();
    let cipher = Aes256CbcEnc::new(key.into(), iv.into());
    cipher
        .encrypt_padded_mut::<aes::cipher::block_padding::NoPadding>(&mut padded, len)
        .map_err(|_| "Encryption failed")?;

    Ok(padded)
}

/// Decrypt data using AES-256 in CBC mode and remove PKCS#7 padding.
///
/// Used for PDF 2.0 encryption (V=5, R=5/6).
///
/// # Arguments
///
/// * `key` - The 32-byte encryption key
/// * `iv` - The 16-byte initialization vector
/// * `data` - The encrypted data
///
/// # Returns
///
/// The decrypted data with padding removed, or an error if decryption fails.
/// If PKCS#7 padding is invalid (common in real-world PDFs), the raw decrypted
/// data is returned without unpadding rather than failing.
#[allow(dead_code)]
pub fn aes256_decrypt(key: &[u8], iv: &[u8], data: &[u8]) -> Result<Vec<u8>, &'static str> {
    if key.len() != 32 {
        return Err("AES-256 key must be 32 bytes");
    }
    if iv.len() != 16 {
        return Err("IV must be 16 bytes");
    }
    if data.is_empty() {
        return Ok(Vec::new());
    }
    if !data.len().is_multiple_of(16) {
        return Err("Encrypted data length must be multiple of 16");
    }

    // Decrypt in-place
    let mut buffer = data.to_vec();
    let cipher = Aes256CbcDec::new(key.into(), iv.into());
    let decrypted = cipher
        .decrypt_padded_mut::<aes::cipher::block_padding::NoPadding>(&mut buffer)
        .map_err(|_| "Decryption failed")?;

    Ok(strip_pkcs7_padding(decrypted).to_vec())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_aes128_round_trip() {
        let key = b"0123456789abcdef"; // 16 bytes
        let iv = b"fedcba9876543210"; // 16 bytes
        let plaintext = b"Hello, AES encryption!";

        // Encrypt
        let ciphertext = aes128_encrypt(key, iv, plaintext).unwrap();

        // Decrypt
        let decrypted = aes128_decrypt(key, iv, &ciphertext).unwrap();

        assert_eq!(plaintext, &decrypted[..]);
        assert_ne!(plaintext, &ciphertext[..]);
    }

    #[test]
    fn test_aes128_empty() {
        let key = b"0123456789abcdef";
        let iv = b"fedcba9876543210";
        let plaintext = b"";

        let ciphertext = aes128_encrypt(key, iv, plaintext).unwrap();
        let decrypted = aes128_decrypt(key, iv, &ciphertext).unwrap();

        assert_eq!(decrypted.len(), 0);
    }

    #[test]
    fn test_aes128_block_aligned() {
        let key = b"0123456789abcdef";
        let iv = b"fedcba9876543210";
        let plaintext = b"Exactly16bytes!!"; // 16 bytes

        let ciphertext = aes128_encrypt(key, iv, plaintext).unwrap();
        let decrypted = aes128_decrypt(key, iv, &ciphertext).unwrap();

        assert_eq!(plaintext, &decrypted[..]);
    }

    #[test]
    fn test_aes128_invalid_key() {
        let key = b"short"; // Too short
        let iv = b"fedcba9876543210";
        let plaintext = b"data";

        assert!(aes128_encrypt(key, iv, plaintext).is_err());
    }

    #[test]
    fn test_aes128_different_keys() {
        let iv = b"fedcba9876543210";
        let plaintext = b"Secret message";

        let key1 = b"key1key1key1key1";
        let key2 = b"key2key2key2key2";

        let encrypted1 = aes128_encrypt(key1, iv, plaintext).unwrap();
        let encrypted2 = aes128_encrypt(key2, iv, plaintext).unwrap();

        // Different keys should produce different ciphertexts
        assert_ne!(encrypted1, encrypted2);
    }
}
