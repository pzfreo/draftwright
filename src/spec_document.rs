//! Buffer a Spec for version preflight without discarding duplicate object members.
//! Replaying maps lets each typed field's deserializer enforce its own conflict rules.
use serde::de::{IntoDeserializer, MapAccess, SeqAccess, Visitor, value};
use serde::{Deserialize, Deserializer};
use serde_json::Value;

pub(crate) enum Document {
    Scalar(Value),
    Array(Vec<Self>),
    Object(Vec<(String, Self)>),
}

impl Document {
    pub(crate) fn get(&self, key: &str) -> Option<&Self> {
        match self {
            Self::Object(entries) => entries.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }
    pub(crate) fn as_str(&self) -> Option<&str> {
        match self {
            Self::Scalar(v) => v.as_str(),
            _ => None,
        }
    }
    pub(crate) fn is_null(&self) -> bool {
        matches!(self, Self::Scalar(Value::Null))
    }
}

impl<'de> Deserialize<'de> for Document {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct Read;
        impl<'de> Visitor<'de> for Read {
            type Value = Document;
            fn expecting(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                f.write_str("a Spec document")
            }
            fn visit_unit<E: serde::de::Error>(self) -> Result<Document, E> {
                Ok(Document::Scalar(Value::Null))
            }
            fn visit_bool<E: serde::de::Error>(self, v: bool) -> Result<Document, E> {
                Ok(Document::Scalar(v.into()))
            }
            fn visit_i64<E: serde::de::Error>(self, v: i64) -> Result<Document, E> {
                Ok(Document::Scalar(v.into()))
            }
            fn visit_u64<E: serde::de::Error>(self, v: u64) -> Result<Document, E> {
                Ok(Document::Scalar(v.into()))
            }
            fn visit_f64<E: serde::de::Error>(self, v: f64) -> Result<Document, E> {
                Ok(Document::Scalar(v.into()))
            }
            fn visit_str<E: serde::de::Error>(self, v: &str) -> Result<Document, E> {
                self.visit_string(v.into())
            }
            fn visit_string<E: serde::de::Error>(self, v: String) -> Result<Document, E> {
                Ok(Document::Scalar(v.into()))
            }
            fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Document, A::Error> {
                let mut entries = Vec::new();
                while let Some(v) = seq.next_element()? {
                    entries.push(v);
                }
                Ok(Document::Array(entries))
            }
            fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Document, A::Error> {
                let mut entries = Vec::new();
                while let Some(entry) = map.next_entry()? {
                    entries.push(entry);
                }
                Ok(Document::Object(entries))
            }
        }
        d.deserialize_any(Read)
    }
}

impl<'de> IntoDeserializer<'de, serde_json::Error> for Document {
    type Deserializer = Self;
    fn into_deserializer(self) -> Self {
        self
    }
}

impl<'de> Deserializer<'de> for Document {
    type Error = serde_json::Error;
    fn deserialize_option<V: Visitor<'de>>(self, visitor: V) -> Result<V::Value, Self::Error> {
        if self.is_null() {
            visitor.visit_none()
        } else {
            visitor.visit_some(self)
        }
    }
    fn deserialize_newtype_struct<V: Visitor<'de>>(
        self,
        _name: &'static str,
        visitor: V,
    ) -> Result<V::Value, Self::Error> {
        visitor.visit_newtype_struct(self)
    }
    fn deserialize_enum<V: Visitor<'de>>(
        self,
        name: &'static str,
        variants: &'static [&'static str],
        visitor: V,
    ) -> Result<V::Value, Self::Error> {
        match self {
            Self::Scalar(v) => v
                .into_deserializer()
                .deserialize_enum(name, variants, visitor),
            Self::Object(v) => {
                value::MapDeserializer::new(v.into_iter()).deserialize_enum(name, variants, visitor)
            }
            other => other.deserialize_any(visitor),
        }
    }
    fn deserialize_any<V: Visitor<'de>>(self, visitor: V) -> Result<V::Value, Self::Error> {
        match self {
            Self::Scalar(v) => v.into_deserializer().deserialize_any(visitor),
            Self::Array(v) => visitor.visit_seq(value::SeqDeserializer::new(v.into_iter())),
            Self::Object(v) => visitor.visit_map(value::MapDeserializer::new(v.into_iter())),
        }
    }
    serde::forward_to_deserialize_any! {
        bool i8 i16 i32 i64 u8 u16 u32 u64 f32 f64 char str string bytes byte_buf
        unit unit_struct seq tuple tuple_struct map struct
        identifier ignored_any
    }
}
